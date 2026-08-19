"""The GW1 gap: asking the manager for their 15, once, and confirming it back.

Before a gameweek kicks off, FPL publishes nobody's picks. The endpoint that
would show them needs the account password, which this engine does not have and
will not obtain. That leaves exactly one honest route into the pre-season squad:
ask.

The design constraints are the ones that make a manual-entry path either useful
or a liability:

* **Once.** The manager types their 15 one time. From the moment the gameweek
  starts, :mod:`fpl_edge.myteam.state` reads the public picks endpoint instead
  and the manual record becomes a historical artefact, not a source of truth.
* **Robust to how people actually type.** Any order, one per line or comma
  separated, numbered or bulleted, with or without the price and position that
  the FPL app puts next to a name. "Gabriel", "gabriel magalhaes", "G.Jesus" and
  "alexander arnold" all have to land, because a manager pasting a squad off
  their phone is not going to match a canonical spelling.
* **Never a silent guess.** Two players matching a fragment equally well is a
  question, not a coin flip. :class:`~fpl_edge.interfaces.parsing.PlayerResolver`
  already draws that line correctly and is reused rather than reimplemented, so
  the bot's name matching and this cannot drift apart.
* **Confirmed before it counts.** A draft is staged with a short token and shown
  back in full -- names, positions, prices, clubs, the total and the bank -- and
  nothing is saved until the manager confirms. A mistyped squad that silently
  becomes the engine's model of reality would poison every recommendation that
  follows, and would do it invisibly.

The staged draft is validated by :func:`fpl_edge.eval.replay.apply_decision`
before it is ever shown, so an illegal 15 is rejected with the actual rule it
broke rather than accepted and discovered later.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from fpl_edge.eval.replay import Decision, InvalidDecision, apply_decision
from fpl_edge.eval.scoring import Chip, Pick
from fpl_edge.interfaces.parsing import PlayerResolver, Resolution
from fpl_edge.myteam.state import PlayerIndex
from fpl_edge.rules import rules
from fpl_edge.types import GwId, Money, Position

UTC = dt.timezone.utc

#: Lines that are obviously not a player name. A pasted squad carries the app's
#: own furniture, and a header is better dropped than resolved to a player.
_NOISE = re.compile(
    r"^(?:my\s+)?(?:squad|team|side|xi|lineup|line-up|starting(?:\s+xi)?|bench|"
    r"subs?|substitutes?|captain|vice|goalkeepers?|defenders?|midfielders?|"
    r"forwards?|attackers?|strikers?|gk|gkp|def|mid|fwd|total|cost|bank|value|"
    r"itb|in the bank|free transfers?|this is (?:my|the) team)\s*[:\-]?\s*$",
    re.IGNORECASE,
)

#: A leading list marker: "1.", "1)", "-", "*", "•".
_BULLET = re.compile(r"^\s*(?:\d{1,2}\s*[.)\]]|[-*•–])\s*")

#: Captaincy marks. Carry no identity, so they are simply removed.
_CAPTAIN_MARK = re.compile(r"\s*\((?:c|vc|captain|vice)\)\s*$", re.IGNORECASE)

#: A trailing price, with or without the pound sign and the parentheses.
#: Kept rather than discarded: it is the cheapest disambiguator there is, and
#: every squad pasted out of the FPL app has one next to each name.
_PRICE = re.compile(
    r"\s*[(\[]?\s*£?\s*(\d{1,2}(?:\.\d)?)\s*m?\s*[)\]]?\s*$", re.IGNORECASE
)
_PRICE_LEADING_DASH = re.compile(r"\s*[-–]\s*$")

#: A position tag, leading ("MID: Hughes") or trailing ("Hughes (MID)").
_POS_WORDS = {
    "gk": Position.GKP, "gkp": Position.GKP, "goalkeeper": Position.GKP,
    "def": Position.DEF, "defender": Position.DEF,
    "mid": Position.MID, "midfielder": Position.MID,
    "fwd": Position.FWD, "fw": Position.FWD, "st": Position.FWD,
    "forward": Position.FWD, "striker": Position.FWD,
}
_LEADING_POS = re.compile(
    r"^(gkp?|goalkeeper|def|defender|mid|midfielder|fwd|fw|st|forward|striker)"
    r"\s*[:\-–]\s*", re.IGNORECASE
)
_TRAILING_POS = re.compile(
    r"\s*[(\[]\s*(gkp?|goalkeeper|def|defender|mid|midfielder|fwd|fw|st|forward|"
    r"striker)\s*[)\]]\s*$", re.IGNORECASE
)


class ManualEntryError(ValueError):
    """The pasted squad cannot be turned into a legal 15."""


@dataclass(frozen=True, slots=True)
class Fragment:
    """One entry from a pasted squad, split into a name and its annotations.

    The annotations are not thrown away. "Hughes (£4.5m)" and "Hughes (MID)"
    both name a specific one of the two Premier League players called Hughes,
    and a parser that strips the qualifier and then asks "which Hughes?" is
    being obtuse about information the manager already gave it.
    """

    text: str                      # what was typed, for error messages
    name: str                      # the part to resolve
    position: Position | None = None
    price_tenths: int | None = None

    @property
    def has_qualifier(self) -> bool:
        return self.position is not None or self.price_tenths is not None

    def describe(self) -> str:
        bits = [self.name]
        if self.position is not None:
            bits.append(self.position.name)
        if self.price_tenths is not None:
            bits.append(str(Money(self.price_tenths)))
        return " ".join(bits)


def split_fragments(text: str) -> list[Fragment]:
    """Break a pasted squad into candidate entries.

    Newlines first, then commas and semicolons *within* a line, because a squad
    is normally pasted one player per line but is just as often typed as a single
    comma-separated run. Slashes are deliberately not split on: several real
    surnames contain one and none of the separators people actually use do.
    """
    out: list[Fragment] = []
    for raw_line in text.replace("\r", "\n").split("\n"):
        for chunk in re.split(r"[,;]|\s{3,}|\s+\|\s+", raw_line):
            parsed = clean_fragment(chunk)
            if parsed is not None:
                out.append(parsed)
    return out


def clean_fragment(chunk: str) -> Fragment | None:
    """Split one entry into a name plus any position/price qualifier."""
    original = chunk.strip()
    if not original:
        return None
    text = _BULLET.sub("", original)
    text = _CAPTAIN_MARK.sub("", text)

    position: Position | None = None
    hit = _LEADING_POS.match(text)
    if hit:
        position = _POS_WORDS[hit.group(1).lower()]
        text = text[hit.end():]
    hit = _TRAILING_POS.search(text)
    if hit:
        position = _POS_WORDS[hit.group(1).lower()]
        text = text[: hit.start()]

    price_tenths: int | None = None
    hit = _PRICE.search(text)
    # Only treat a trailing number as a price when something is left in front of
    # it. "4.5" alone is furniture; "Shaw 4.5" is a priced player.
    if hit and text[: hit.start()].strip():
        try:
            price_tenths = Money.from_millions(float(hit.group(1))).tenths
        except ValueError:
            price_tenths = None
        if price_tenths is not None:
            text = text[: hit.start()]
            text = _PRICE_LEADING_DASH.sub("", text)

    text = text.strip(" \t.-–|:")
    if not text or _NOISE.match(text):
        return None
    # A bare number, a percentage or a lone price is furniture, not a name.
    if re.fullmatch(r"[£\d.,%/mkxc()\s-]+", text, re.IGNORECASE):
        return None
    return Fragment(
        text=original, name=text, position=position, price_tenths=price_tenths
    )


@dataclass(frozen=True, slots=True)
class FragmentResolution:
    """One typed fragment and what the resolver made of it.

    ``code`` is None whenever a question is owed. Two players matching equally
    well is a question; a fragment that matched nothing *and had no near misses*
    is furniture, which is a different thing and is reported as ``ignored``.
    """

    fragment: Fragment
    resolution: Resolution
    code: int | None = None
    disambiguated_by: str = ""

    @property
    def ignored(self) -> bool:
        """Matched nothing and looked nothing like anybody: a stray header."""
        return (
            self.code is None
            and not self.resolution.matched
            and not self.resolution.candidates
        )

    @property
    def needs_a_question(self) -> bool:
        return self.code is None and not self.ignored

    def question(self) -> str:
        r = self.resolution
        text = self.fragment.text.strip()
        if r.ambiguous:
            options = "\n".join(f"    - {c.label} ({c.hint})" for c in r.candidates)
            return (
                f"  {text!r} matches more than one player:\n{options}\n"
                f"    Add the price or position to say which, e.g. "
                f"'{self.fragment.name} (MID)'."
            )
        options = "\n".join(f"    - {c.label} ({c.hint})" for c in r.candidates)
        return f"  {text!r} matched nothing. Did you mean:\n{options}"


def resolve_fragment(
    fragment: Fragment, resolver: PlayerResolver, index: PlayerIndex
) -> FragmentResolution:
    """Resolve one fragment, using its own qualifier to break a tie.

    The qualifier only ever *narrows* an existing candidate set. It cannot
    promote a player the resolver did not think matched, so a wrong position tag
    produces a question rather than a wrong player.
    """
    resolution = resolver.resolve(fragment.name)
    best = resolution.best
    if best is not None:
        return FragmentResolution(fragment=fragment, resolution=resolution, code=int(best.code))

    if not resolution.ambiguous or not fragment.has_qualifier:
        return FragmentResolution(fragment=fragment, resolution=resolution)

    survivors = []
    for candidate in resolution.candidates:
        code = int(candidate.code)
        if fragment.position is not None and index.position.get(code) is not fragment.position:
            continue
        if (
            fragment.price_tenths is not None
            and int(index.price_now.get(code, -1)) != fragment.price_tenths
        ):
            continue
        survivors.append(code)
    if len(survivors) != 1:
        return FragmentResolution(fragment=fragment, resolution=resolution)
    how = ", ".join(
        bit for bit in (
            fragment.position.name if fragment.position else "",
            str(Money(fragment.price_tenths)) if fragment.price_tenths is not None else "",
        ) if bit
    )
    return FragmentResolution(
        fragment=fragment, resolution=resolution, code=survivors[0], disambiguated_by=how,
    )


@dataclass(frozen=True, slots=True)
class ManualSquadRecord:
    """A confirmed manual squad. The thing that gets persisted.

    Stores the purchase price of every player alongside the code, because that
    is the number the sell-on fee is measured against for the entire season and
    it cannot be recovered later: once prices move, today's price no longer
    tells you what was paid.
    """

    entry_id: int
    season: str
    gw: GwId
    codes: tuple[int, ...]
    bought_at: Mapping[int, int]
    order: Mapping[int, int]              # code -> 1..15 slot
    captain: int
    vice: int
    bank_tenths: int
    entered_utc: dt.datetime
    confirmed_utc: dt.datetime | None = None
    source: str = "cli"
    #: True while the XI, captain and vice are the placeholder legal arrangement
    #: this module picked rather than the manager's or the optimiser's choice.
    provisional_lineup: bool = True

    @property
    def digest(self) -> str:
        """Short, stable id for the squad itself. Used as the confirm token."""
        payload = f"{self.entry_id}|{self.season}|{int(self.gw)}|" + ",".join(
            f"{c}:{self.bought_at[c]}" for c in sorted(self.codes)
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:8]

    @property
    def confirmed(self) -> bool:
        return self.confirmed_utc is not None

    def to_picks(self, index: PlayerIndex) -> tuple[Pick, ...]:
        return tuple(
            sorted(
                (
                    Pick(
                        code=code,
                        position=index.position[code],
                        order=int(self.order[code]),
                        is_captain=code == self.captain,
                        is_vice=code == self.vice,
                    )
                    for code in self.codes
                ),
                key=lambda p: p.order,
            )
        )

    def to_json(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "season": self.season,
            "gw": int(self.gw),
            "codes": [int(c) for c in self.codes],
            "bought_at": {str(k): int(v) for k, v in self.bought_at.items()},
            "order": {str(k): int(v) for k, v in self.order.items()},
            "captain": int(self.captain),
            "vice": int(self.vice),
            "bank_tenths": int(self.bank_tenths),
            "entered_utc": self.entered_utc.isoformat(),
            "confirmed_utc": self.confirmed_utc.isoformat() if self.confirmed_utc else None,
            "source": self.source,
            "provisional_lineup": bool(self.provisional_lineup),
            "digest": self.digest,
        }

    @classmethod
    def from_json(cls, body: dict) -> "ManualSquadRecord":
        def _ts(value):
            return dt.datetime.fromisoformat(value).astimezone(UTC) if value else None

        record = cls(
            entry_id=int(body["entry_id"]),
            season=str(body["season"]),
            gw=GwId(int(body["gw"])),
            codes=tuple(int(c) for c in body["codes"]),
            bought_at={int(k): int(v) for k, v in body["bought_at"].items()},
            order={int(k): int(v) for k, v in body["order"].items()},
            captain=int(body["captain"]),
            vice=int(body["vice"]),
            bank_tenths=int(body["bank_tenths"]),
            entered_utc=_ts(body["entered_utc"]),
            confirmed_utc=_ts(body.get("confirmed_utc")),
            source=str(body.get("source", "cli")),
            provisional_lineup=bool(body.get("provisional_lineup", True)),
        )
        stored = body.get("digest")
        if stored and stored != record.digest:
            raise ManualEntryError(
                f"stored manual squad digest {stored} does not match its contents "
                f"({record.digest}). The file has been edited by hand or corrupted; "
                "re-enter the squad rather than trusting it."
            )
        return record


@dataclass(frozen=True, slots=True)
class SquadDraft:
    """A parsed, validated, not-yet-saved squad, plus everything wrong with it."""

    entry_id: int
    season: str
    gw: GwId
    fragments: tuple[FragmentResolution, ...]
    record: ManualSquadRecord | None
    problems: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.record is not None and not self.problems and not self.questions

    @property
    def token(self) -> str | None:
        return self.record.digest if self.record is not None else None

    def render(self, index: PlayerIndex) -> str:
        """The confirmation the manager reads before anything is saved."""
        lines: list[str] = []
        if self.questions:
            lines.append("I need you to settle these before I can save anything:")
            lines.extend(self.questions)
            lines.append("")
        if self.problems:
            lines.append("That squad is not legal:")
            lines.extend(f"  - {p}" for p in self.problems)
            lines.append("")
        if self.record is None:
            # The notes matter most in this branch: "13 recognised, add 2" is
            # only actionable once you can see which two lines I threw away.
            if self.notes:
                lines.extend(f"  note: {n}" for n in self.notes)
                lines.append("")
            lines.append("Nothing saved. Send the 15 again with the fixes.")
            return "\n".join(lines).rstrip()

        rec = self.record
        lines.append(f"Check this is your {self.season} GW{int(self.gw)} squad:")
        lines.append("")
        by_slot = sorted(rec.codes, key=lambda c: rec.order[c])
        for code in by_slot:
            slot = rec.order[code]
            tag = "  " if slot <= 11 else "B "
            mark = " (C)" if code == rec.captain else (" (V)" if code == rec.vice else "")
            lines.append(
                f"  {tag}{index.position[code].name} {index.name[code]:<18} "
                f"{Money(rec.bought_at[code])}{mark}"
            )
        spend = Money(sum(rec.bought_at.values()))
        lines.append("")
        lines.append(f"  cost {spend}, bank {Money(rec.bank_tenths)}, "
                     f"total {Money(spend.tenths + rec.bank_tenths)}")
        if self.notes:
            lines.append("")
            lines.extend(f"  note: {n}" for n in self.notes)
        lines.append("")
        lines.append(f"Reply `confirm {rec.digest}` if that is right. Nothing is saved until you do.")
        return "\n".join(lines)


# -- building a draft --------------------------------------------------------


def _legal_lineup(codes: Sequence[int], index: PlayerIndex) -> tuple[dict[int, int], int, int]:
    """A legal XI, bench order, captain and vice -- provisional, by construction.

    This module's job is to capture *which fifteen players you own*, not to pick
    the team. But :func:`apply_decision` rightly refuses to validate a squad
    without a legal formation and a captain, so a deterministic legal arrangement
    is generated here and flagged as provisional. The optimiser owns the real
    XI, captain and bench order, and overwrites this the moment it runs.

    Most expensive first within each position, which is a placeholder heuristic
    and nothing more.
    """
    r = rules()
    mn = {Position[k]: int(v) for k, v in r.get("squad.min_play_by_position").items()}
    mx = {Position[k]: int(v) for k, v in r.get("squad.max_play_by_position").items()}
    xi_size = int(r.get("squad.starting_xi"))

    by_pos: dict[Position, list[int]] = {p: [] for p in Position}
    for code in codes:
        by_pos[index.position[code]].append(code)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda c: (-index.price_now.get(c, 0), c))

    starters: list[int] = []
    for pos in (Position.GKP, Position.DEF, Position.MID, Position.FWD):
        starters.extend(by_pos[pos][: mn[pos]])
    # Fill the remaining XI slots with the best available, respecting the caps.
    counts = {pos: sum(1 for c in starters if index.position[c] is pos) for pos in Position}
    pool = [
        c for pos in (Position.DEF, Position.MID, Position.FWD) for c in by_pos[pos]
        if c not in starters
    ]
    pool.sort(key=lambda c: (-index.price_now.get(c, 0), c))
    for code in pool:
        if len(starters) >= xi_size:
            break
        pos = index.position[code]
        if counts[pos] < mx[pos]:
            starters.append(code)
            counts[pos] += 1
    if len(starters) != xi_size:
        raise ManualEntryError(
            f"cannot build a legal XI from these 15 ({len(starters)} placed). "
            "The position split is wrong."
        )

    bench = [c for c in codes if c not in starters]
    # Reserve keeper first: a keeper can only ever be replaced by a keeper, so
    # any other bench slot for him wastes a substitution priority.
    bench.sort(key=lambda c: (index.position[c] is not Position.GKP,
                              -index.price_now.get(c, 0), c))
    order = {code: i + 1 for i, code in enumerate(
        sorted(starters, key=lambda c: (index.position[c], -index.price_now.get(c, 0), c))
    )}
    for i, code in enumerate(bench):
        order[code] = xi_size + 1 + i

    ranked = sorted(starters, key=lambda c: (-index.price_now.get(c, 0), c))
    return order, ranked[0], ranked[1]


def build_draft(
    text: str,
    *,
    resolver: PlayerResolver,
    index: PlayerIndex,
    entry_id: int,
    season: str,
    gw: int,
    now: dt.datetime,
    source: str = "cli",
) -> SquadDraft:
    """Parse, resolve, arrange and validate a pasted squad. Saves nothing."""
    fragments = split_fragments(text)
    resolved = tuple(resolve_fragment(f, resolver, index) for f in fragments)

    squad_size = int(rules().get("squad.size"))
    questions = tuple(fr.question() for fr in resolved if fr.needs_a_question)
    codes = [fr.code for fr in resolved if fr.code is not None]

    problems: list[str] = []
    extra_notes: list[str] = []
    ignored = [fr.fragment.text.strip() for fr in resolved if fr.ignored]
    if ignored:
        extra_notes.append(
            "ignored as not-a-name (matched nobody, not even closely): "
            + ", ".join(repr(x) for x in ignored)
        )
    for fr in resolved:
        if fr.disambiguated_by:
            extra_notes.append(
                f"{fr.fragment.name!r} was ambiguous; your {fr.disambiguated_by} "
                f"picked {index.name.get(fr.code, fr.code)}"
            )
    seen: set[int] = set()
    duplicates: list[str] = []
    unique: list[int] = []
    for code in codes:
        if code in seen:
            duplicates.append(index.name.get(code, str(code)))
            continue
        seen.add(code)
        unique.append(code)
    if duplicates:
        problems.append(f"named twice: {', '.join(sorted(set(duplicates)))}")
    if not questions and len(unique) != squad_size:
        problems.append(
            f"{len(unique)} player(s) recognised, and a squad is exactly {squad_size}. "
            f"{'Add' if len(unique) < squad_size else 'Remove'} "
            f"{abs(squad_size - len(unique))}."
        )
    if questions or problems:
        return SquadDraft(
            entry_id=entry_id, season=season, gw=GwId(int(gw)),
            fragments=resolved, record=None,
            problems=tuple(problems), questions=questions,
            notes=tuple(extra_notes),
        )

    try:
        order, captain, vice = _legal_lineup(unique, index)
    except ManualEntryError as exc:
        return SquadDraft(
            entry_id=entry_id, season=season, gw=GwId(int(gw)),
            fragments=resolved, record=None, problems=(str(exc),),
            notes=tuple(extra_notes),
        )

    # Pre-season the purchase price is simply the price today: the registry is
    # explicit that prices do not move before the season starts.
    bought_at = {code: int(index.price_now[code]) for code in unique}
    picks = tuple(
        sorted(
            (
                Pick(
                    code=code,
                    position=index.position[code],
                    order=order[code],
                    is_captain=code == captain,
                    is_vice=code == vice,
                )
                for code in unique
            ),
            key=lambda p: p.order,
        )
    )

    # The real rule enforcement, not a reimplementation of it.
    try:
        checked, _hits, _out, _into = apply_decision(
            None, Decision(picks=picks, chip=Chip.NONE),
            bought_at, dict(index.team_code), GwId(int(gw)),
        )
    except InvalidDecision as exc:
        return SquadDraft(
            entry_id=entry_id, season=season, gw=GwId(int(gw)),
            fragments=resolved, record=None,
            problems=(f"{exc}",),
            notes=(*extra_notes,
                   "checked by fpl_edge.eval.replay.apply_decision, the same code "
                   "the backtest uses"),
        )

    record = ManualSquadRecord(
        entry_id=entry_id,
        season=season,
        gw=GwId(int(gw)),
        codes=tuple(unique),
        bought_at=bought_at,
        order=order,
        captain=captain,
        vice=vice,
        bank_tenths=checked.bank_tenths,
        entered_utc=now,
        source=source,
        provisional_lineup=True,
    )
    return SquadDraft(
        entry_id=entry_id, season=season, gw=GwId(int(gw)),
        fragments=resolved, record=record,
        notes=(
            *extra_notes,
            "XI, captain and bench order are a placeholder legal arrangement, not "
            "a recommendation -- the optimiser picks those.",
            "purchase prices are today's prices; the rule registry confirms prices "
            "do not move before the season starts.",
        ),
    )


# -- reconciliation against the public picks ---------------------------------


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """Manual record versus what FPL actually published once the gameweek began.

    A mismatch is reported, never silently resolved. The public picks are the
    truth about who was owned, but *why* they differ matters: a transfer made
    after the squad was entered is expected and harmless, whereas a player the
    manager never owned means the manual entry was wrong and every
    recommendation made off it was made off a fiction.
    """

    gw: GwId
    agreed: tuple[int, ...]
    only_manual: tuple[int, ...]
    only_public: tuple[int, ...]
    transfers_between: int

    @property
    def matches(self) -> bool:
        return not self.only_manual and not self.only_public

    @property
    def explained_by_transfers(self) -> bool:
        return len(self.only_manual) == len(self.only_public) <= self.transfers_between

    def render(self, index: PlayerIndex) -> str:
        if self.matches:
            return (
                f"GW{int(self.gw)}: the squad you entered matches the 15 FPL "
                f"published. Switching to the public picks endpoint from here."
            )
        names = lambda codes: ", ".join(index.name.get(c, str(c)) for c in codes)  # noqa: E731
        head = (
            f"GW{int(self.gw)}: the squad you entered does NOT match the 15 FPL "
            f"published."
        )
        body = [
            head,
            f"  you told me, FPL disagrees: {names(self.only_manual) or '(none)'}",
            f"  FPL has, you did not say:   {names(self.only_public) or '(none)'}",
        ]
        if self.explained_by_transfers:
            body.append(
                f"  {self.transfers_between} transfer(s) were made after you entered "
                "the squad, which accounts for the difference."
            )
        else:
            body.append(
                "  This is NOT explained by transfers made since. The manual entry "
                "was wrong. The public picks are authoritative and are what the "
                "engine will use; nothing has been overwritten silently."
            )
        return "\n".join(body)


def reconcile(
    record: ManualSquadRecord,
    public_codes: Sequence[int],
    *,
    gw: int,
    transfers_between: int = 0,
) -> Reconciliation:
    manual = set(int(c) for c in record.codes)
    public = set(int(c) for c in public_codes)
    return Reconciliation(
        gw=GwId(int(gw)),
        agreed=tuple(sorted(manual & public)),
        only_manual=tuple(sorted(manual - public)),
        only_public=tuple(sorted(public - manual)),
        transfers_between=int(transfers_between),
    )
