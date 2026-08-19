"""Turning what the user typed into what the user meant.

The input is a text message written one-handed during highlights. It has
nicknames ("kdb"), missing accents ("odegaard"), missing gameweeks, missing
verbs, and surnames shared by two players in the same league. The parser's job is
to get it right or to say it cannot.

The design commitment that matters: **ambiguity is resolved by asking, never by
ranking**. It is tempting to break a "Palmer" tie toward Cole rather than Alex on
ownership, and it would be right most of the time. But when it is wrong the user
never finds out -- the idea sits in the registry attributed to a backup
goalkeeper, and both the hit rate and the bias analysis are quietly poisoned by a
thought the user never had. So ownership orders the candidate list for the
clarification prompt and has no vote in whether to ask. The cost of asking is one
round trip; the cost of guessing wrong is unfalsifiable data.

Everything in this module treats the message as inert text. It is normalised,
regex-matched and string-compared. It is never evaluated, never interpolated into
SQL, and never dispatched as a command.
"""

from __future__ import annotations

import datetime as dt
import functools
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

import pandas as pd

from fpl_edge.interfaces.ideas import (
    DEFAULT_COMPARATOR,
    DEFAULT_HORIZON,
    CandidateMatch,
    Comparator,
    IdeaKind,
)
from fpl_edge.types import GwId, PlayerCode

#: A candidate below this score is not a plausible reading of the text at all.
#: Set from the failure it prevents: at 0.62, "kdb" (whose target is not in
#: the 2026-27 league) fuzzy-matched "De Cuyper" at 0.63 and logged an idea
#: about a £4.5m full-back. A near-miss must produce a question, not a player.
MIN_MATCH_SCORE = 0.70

#: If the runner-up is within this of the leader, the text does not determine a
#: player and we ask. Tuned so that "palmer" (two real Palmers) asks while
#: "semenyo" (one) does not.
AMBIGUITY_MARGIN = 0.08

#: How many alternatives to offer in a clarification. More than this and the
#: user is being asked to do the parser's job.
MAX_CANDIDATES = 5

#: Community shorthand that no amount of string distance will ever recover,
#: because the letters are not in the name. Maps alias -> a search string that
#: the fuzzy resolver can then handle normally, so this table does not need to
#: know player codes and does not go stale when a player moves club.
NICKNAMES: dict[str, str] = {
    "kdb": "de bruyne",
    "vvd": "van dijk",
    "taa": "alexander-arnold",
    "trent": "alexander-arnold",
    "bruno": "bruno fernandes",
    "big dog": "watkins",
    "the egyptian king": "salah",
    "sonny": "son",
    "g9": "gabriel jesus",
    "jgp": "joao pedro",
    "coleslaw": "cole palmer",
    "cole": "cole palmer",
    "the beard": "gvardiol",
}

#: Words that carry intent. Ordered by specificity: CAPTAIN wins over
#: TRANSFER_IN because "get X in and captain him" is a captaincy call.
#:
#: Matched on WORD BOUNDARIES, not as substrings. Plain `in` testing looks
#: harmless and is not: "out" and "off" both occur inside "about", so "thinking
#: about Van de Ven" classified as a FADE and the engine recorded the user
#: predicting the opposite of what they said. A misclassified kind flips the
#: thesis, flips the resolution rule, and scores a correct call as wrong.
_INTENT_PATTERNS: tuple[tuple[IdeaKind, tuple[str, ...]], ...] = (
    (IdeaKind.CAPTAIN, ("captain", "captaincy", "armband", "cap", "c", "triple captain", "tc")),
    (IdeaKind.FADE, ("avoid", "fade", "sell", "drop", "bench", "get rid", "ditch", "off", "out")),
    (IdeaKind.DIFFERENTIAL, ("differential", "diff", "punt", "under the radar", "nobody owns")),
    (
        IdeaKind.TRANSFER_IN,
        ("bring in", "transfer in", "buy", "get", "sign", "in for", "want", "need"),
    ),
)

#: Soft signals: "I like X" is an opinion, not a transfer plan, but it is still a
#: bullish claim and deserves the same comparator as one.
_BULLISH = ("like", "likes", "love", "loves", "fancy", "rate", "backing", "into", "keen")


@functools.lru_cache(maxsize=256)
def _phrase(needle: str) -> re.Pattern[str]:
    """Word-boundary matcher for one intent phrase."""
    return re.compile(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])")

_GW_PATTERNS = (
    re.compile(r"\bgw\s*[-#]?\s*(\d{1,2})\b"),
    re.compile(r"\bgame\s*week\s*[-#]?\s*(\d{1,2})\b"),
    re.compile(r"\bweek\s*[-#]?\s*(\d{1,2})\b"),
    re.compile(r"\bdgw\s*[-#]?\s*(\d{1,2})\b"),
)
_RELATIVE_GW = re.compile(r"\b(this|next|coming|upcoming)\s+(gw|gameweek|week)\b")

_VERSUS = re.compile(r"\b(?:vs\.?|versus|or)\b")

#: Filler that would otherwise be scored as a player name. Kept small and
#: literal; an over-eager stopword list deletes real surnames (Wood, Long, King).
_STOPWORDS = frozenset(
    """
    i im i'm a an the is are was were do does did to for of in on at and but so
    my me we you he she it they them his her their this that these those
    should shall would could will can may might must am be been being have has
    had think thinking thought like liked liking love loves fancy rate rated
    reckon feel feels about maybe perhaps really very quite pretty just now
    then today tonight tomorrow yesterday good great nice bad poor cheap
    expensive value form fixture fixtures run price points captain captaincy
    armband cap vice transfer transfers buy sell bring get got take put keep
    hold bench start starting play playing player play differential diff punt
    template haul hauls blank blanks who what when why how which
    looks look looking seems seem seemed nailed worth better best decent
    solid fit back going still though anyway definitely surely
    gw gameweek week dgw next coming upcoming vs versus or and yeah yes no ok
    okay hmm hmmm lol help idea ideas thoughts thought view take shout
    """.split()
)


def _fold(text: str) -> str:
    """Lowercase, strip accents, collapse punctuation to spaces.

    Ø -> o and é -> e matter: the user is typing on a phone keyboard that does
    not have those characters, but the FPL API very much does.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    # NFKD leaves a handful of Latin letters undecomposed (ø, ð, æ, ł).
    stripped = (
        stripped.replace("ø", "o").replace("Ø", "o")
        .replace("đ", "d").replace("ð", "d")
        .replace("ł", "l").replace("Ł", "l")
        .replace("æ", "ae").replace("Æ", "ae")
        .replace("ß", "ss").replace("þ", "th")
    )
    stripped = stripped.lower()
    stripped = re.sub(r"[^a-z0-9\s'-]", " ", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


def _tokens(folded: str) -> list[str]:
    return [t for t in re.split(r"[\s'-]+", folded) if t]


def _levenshtein(a: str, b: str, *, cap: int) -> int:
    """Edit distance, abandoned once it exceeds ``cap``.

    Needed because :class:`difflib.SequenceMatcher` similarity is close to
    meaningless on short strings: "salah" and "saliba" score 0.73, comfortably
    above any threshold that also accepts real typos, and Mohamed Salah is not in
    the 2026-27 league. Three edits on a five-letter word is a different player,
    not a misspelling, and only an absolute edit count can tell the difference.
    """
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _max_edits(query: str) -> int:
    """How wrong a typed name may be before it is a different name.

    Scaled by length because one wrong letter in "salah" changes who you mean,
    while two wrong letters in "alexander-arnold" plainly do not.
    """
    n = len(query)
    if n <= 5:
        return 1
    if n <= 9:
        return 2
    return 3


@dataclass(frozen=True, slots=True)
class Resolution:
    """The outcome of trying to turn a name fragment into one player."""

    query: str
    candidates: tuple[CandidateMatch, ...]
    ambiguous: bool
    reason: str = ""
    #: False when nothing cleared MIN_MATCH_SCORE and `candidates` holds mere
    #: suggestions. Separate from `ambiguous` because the two need different
    #: questions ("which of these?" vs "I could not find that player") and
    #: because collapsing them is how a suggestion becomes an accepted guess.
    matched: bool = True

    @property
    def best(self) -> CandidateMatch | None:
        if self.ambiguous or not self.matched or not self.candidates:
            return None
        return self.candidates[0]

    @property
    def confidence(self) -> float:
        return self.candidates[0].score if self.candidates else 0.0


class PlayerResolver:
    """Fuzzy name -> stable :class:`PlayerCode`, over one season's player list.

    Built from ``Snapshot.players``, so the universe is exactly the players who
    existed at the instant the idea was had. Resolving against today's list would
    let a January signing answer an August message.
    """

    def __init__(self, players: pd.DataFrame) -> None:
        if players.empty:
            raise ValueError("PlayerResolver needs a non-empty player list")
        self._df = players.reset_index(drop=True)
        cols = {c for c in self._df.columns}
        first = self._df["first_name"] if "first_name" in cols else pd.Series([""] * len(self._df))
        second = self._df["second_name"] if "second_name" in cols else pd.Series([""] * len(self._df))
        web = self._df["web_name"].astype(str)
        self._names: list[tuple[str, ...]] = []
        for w, f, s in zip(web, first.fillna("").astype(str), second.fillna("").astype(str)):
            forms = {_fold(w), _fold(s), _fold(f"{f} {s}"), _fold(f)}
            # Multi-barrelled surnames: "Pereira de Albuquerque Tavares da Silva"
            # should also be findable as "silva". Trailing tokens only -- leading
            # tokens of a compound surname are rarely how anyone refers to it.
            for base in (_fold(s), _fold(w)):
                parts = _tokens(base)
                if len(parts) > 1:
                    forms.add(parts[-1])
                    forms.add(" ".join(parts[-2:]))
            self._names.append(tuple(sorted(f for f in forms if f)))
        self._own = (
            pd.to_numeric(self._df.get("selected_by_pct"), errors="coerce").fillna(0.0)
            if "selected_by_pct" in cols
            else pd.Series([0.0] * len(self._df))
        )
        self._pos = self._df.get("position", pd.Series([0] * len(self._df)))
        self._price = self._df.get("price_tenths", pd.Series([0] * len(self._df)))

    def __len__(self) -> int:
        return len(self._df)

    @staticmethod
    def _score_one(query: str, qtokens: list[str], name: str) -> float:
        if not name:
            return 0.0
        if name == query:
            return 1.0
        ntokens = _tokens(name)
        if qtokens and qtokens == ntokens:
            return 1.0
        # Whole query is one of the name's words: "dijk" in "virgil van dijk".
        if len(qtokens) == 1 and qtokens[0] in ntokens and len(qtokens[0]) >= 3:
            return 0.95
        # Every query word appears in the name: "bruno fernandes".
        if len(qtokens) > 1 and all(t in ntokens for t in qtokens):
            return 0.93
        if len(query) >= 4 and name.startswith(query):
            return 0.90
        # A prefix that is most of a token is usually a truncation, not a typo,
        # and containment is exact rather than fuzzy, so it skips the edit gate.
        if len(query) >= 4 and any(t.startswith(query) for t in ntokens):
            return 0.86
        # Pure fuzzy matching only survives if the strings are a small, absolute
        # number of edits apart. See _levenshtein for why the ratio alone is not
        # enough to keep "salah" away from "saliba".
        cap = _max_edits(query)
        if min(
            (_levenshtein(query, n, cap=cap) for n in (name, *ntokens)), default=cap + 1
        ) > cap:
            return 0.0
        return SequenceMatcher(None, query, name).ratio()

    def resolve(self, query: str) -> Resolution:
        """Best candidates for a name fragment, or a refusal to choose."""
        folded = _fold(query)
        folded = NICKNAMES.get(folded, folded)
        folded = _fold(folded)
        if not folded:
            return Resolution(query, (), ambiguous=False, reason="empty query")
        qtokens = _tokens(folded)

        scored: list[tuple[float, int]] = []
        for i, forms in enumerate(self._names):
            best = max(self._score_one(folded, qtokens, n) for n in forms)
            if best >= MIN_MATCH_SCORE:
                scored.append((best, i))
        if not scored:
            near = self._nearest(folded, qtokens)
            return Resolution(
                query, near, ambiguous=False,
                reason=f"nothing matches {query!r} above {MIN_MATCH_SCORE}", matched=False,
            )

        # Ownership orders equals for display. It deliberately does not enter the
        # ambiguity test below -- see the module docstring.
        scored.sort(key=lambda si: (-si[0], -float(self._own.iloc[si[1]])))
        top = scored[0][0]
        tied = [s for s in scored if top - s[0] < AMBIGUITY_MARGIN]
        candidates = tuple(self._candidate(i, sc) for sc, i in scored[:MAX_CANDIDATES])

        if len(tied) > 1:
            return Resolution(
                query,
                tuple(self._candidate(i, sc) for sc, i in tied[:MAX_CANDIDATES]),
                ambiguous=True,
                reason=f"{len(tied)} players match {query!r} equally well",
            )
        return Resolution(query, candidates, ambiguous=False)

    def _nearest(self, folded: str, qtokens: list[str]) -> tuple[CandidateMatch, ...]:
        """Best-effort suggestions when nothing clears the bar.

        Offered so a misspelling produces "did you mean" rather than a dead end,
        but flagged by the caller as a no-match so it is never auto-accepted.
        """
        rows = [
            (max(self._score_one(folded, qtokens, n) for n in forms), i)
            for i, forms in enumerate(self._names)
        ]
        rows.sort(key=lambda si: (-si[0], -float(self._own.iloc[si[1]])))
        return tuple(self._candidate(i, sc) for sc, i in rows[:3] if sc > 0.4)

    def _candidate(self, i: int, score: float) -> CandidateMatch:
        row = self._df.iloc[i]
        pos = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}.get(int(row.get("position", 0) or 0), "?")
        price = float(row.get("price_tenths", 0) or 0) / 10.0
        own = float(self._own.iloc[i])
        first = str(row.get("first_name", "") or "").strip()
        second = str(row.get("second_name", "") or "").strip()
        full = f"{first} {second}".strip() or str(row["web_name"])
        return CandidateMatch(
            code=PlayerCode(int(row["code"])),
            label=f"{row['web_name']} ({full})" if full.lower() != str(row["web_name"]).lower() else str(row["web_name"]),
            hint=f"{pos}, £{price:.1f}m, {own:.1f}% owned",
            score=round(float(score), 4),
        )

    def by_code(self, code: int) -> pd.Series | None:
        hit = self._df[self._df["code"] == int(code)]
        return None if hit.empty else hit.iloc[0]


@dataclass(frozen=True, slots=True)
class ParsedMessage:
    """Structured reading of one message. Still just data."""

    raw_text: str
    kind: IdeaKind
    subject: Resolution | None
    rival: Resolution | None
    gw: GwId | None
    gw_was_explicit: bool
    horizon_gws: int
    comparator: Comparator
    name_fragments: tuple[str, ...]


def extract_gw(text: str) -> tuple[int | None, bool]:
    """Pull a gameweek out of free text.

    Returns (gw, was_explicit). A bare integer is deliberately NOT read as a
    gameweek: "Semenyo 7.5" is a price and "3 at the back" is a formation.
    Requiring a gw/week token costs nothing, because when it is missing we fall
    back to the next deadline, which is what the user meant anyway.
    """
    folded = _fold(text)
    for pattern in _GW_PATTERNS:
        m = pattern.search(folded)
        if m:
            gw = int(m.group(1))
            if 1 <= gw <= 38:
                return gw, True
    if _RELATIVE_GW.search(folded):
        return None, False
    return None, False


def classify(text: str) -> IdeaKind:
    """Which kind of claim the message is making."""
    folded = _fold(text)
    if _VERSUS.search(folded) and not folded.startswith("or "):
        # "Salah or Palmer" is a comparison; refined to CAPTAIN below if the
        # message also mentions the armband, since that is the real decision.
        if any(_phrase(k).search(folded) for k in ("captain", "armband", "cap", "tc")):
            return IdeaKind.CAPTAIN
        return IdeaKind.COMPARE
    for kind, needles in _INTENT_PATTERNS:
        if any(_phrase(n).search(folded) for n in needles):
            return kind
    if any(_phrase(b).search(folded) for b in _BULLISH):
        return IdeaKind.TRANSFER_IN
    return IdeaKind.WATCH


#: Any of these means the user is talking about FPL, even if no name resolves.
#: Used only to tell "I meant a player and you missed him" apart from "hi".
_FPL_SIGNALS = tuple(
    sorted(
        {w for _, words in _INTENT_PATTERNS for w in words}
        | set(_BULLISH)
        | {"fpl", "gw", "gameweek", "wildcard", "bench boost", "triple", "chip",
           "hit", "haul", "clean sheet", "xg", "ownership", "template", "vice"}
    )
)


def has_fpl_intent(text: str) -> bool:
    """Does this message look like it is trying to be an FPL idea at all?

    Deliberately separate from :func:`classify`, which always returns *some*
    kind. The distinction it enables is the one between "I could not identify
    that player" and "that was not about a player" -- and the second is now a
    real case, because the first three messages the live bot ever received were
    "/start", "Hi" and "This is the second message". Answering "Hi" with a
    shortlist of defenders whose names are two edits away is worse than useless.
    """
    folded = _fold(text)
    if any(_phrase(sig).search(folded) for sig in _FPL_SIGNALS):
        return True
    return bool(extract_gw(text)[0])


def name_fragments(text: str) -> list[tuple[str, ...]]:
    """Candidate name spans, grouped by the run of text they came from.

    Returns one tuple per contiguous run of non-filler words, **in the order the
    user typed them**, with the alternatives inside each tuple ordered most
    specific first: ``[("b fernandes", "fernandes"), ("odegaard",)]``.

    Text order matters and length order does not. In "Odegaard or B.Fernandes for
    the armband" the subject is Odegaard and the rival is Fernandes, because that
    is the order they were said. Sorting all fragments by length globally -- the
    obvious implementation -- silently swaps them, and the resulting idea says
    the opposite of what the user asked.

    Grouping matters because it stops one run being consumed twice: without it,
    "B.Fernandes" resolves once as "b fernandes" and again as "fernandes", and the
    second hit is mistaken for a second player.
    """
    folded = _fold(text)
    folded = re.sub(r"\bgw\s*[-#]?\s*\d{1,2}\b", " ", folded)
    folded = re.sub(r"\b(game\s*week|week)\s*[-#]?\s*\d{1,2}\b", " ", folded)
    folded = re.sub(r"\b\d+(\.\d+)?m?\b", " ", folded)  # prices, "£7.5m", "12"

    runs: list[list[str]] = [[]]
    for tok in _tokens(folded):
        # Single letters are kept: "b.fernandes" folds to "b fernandes", and
        # dropping the initial turns a message that names exactly one player into
        # an ambiguous one (there are two Fernandes in 2026-27).
        if tok in _STOPWORDS or not tok.isalnum():
            if runs[-1]:
                runs.append([])
            continue
        runs[-1].append(tok)

    groups: list[tuple[str, ...]] = []
    for run in runs:
        if not run:
            continue
        span = " ".join(run)
        alts = [span]
        if len(run) > 1:
            alts.append(" ".join(run[-2:]))  # "kobbie mainoo looks" -> "mainoo looks"
            # Then every single word, in the order typed. Needed because the
            # stopword list will never be complete: "Watkins looks good" leaves
            # the run ["watkins", "looks"], and offering only the span and its
            # TRAILING word asks the resolver about "looks" and never about
            # "watkins" -- so a message naming a real player resolved to nothing.
            alts.extend(run)
        groups.append(tuple(dict.fromkeys(alts)))
    return groups


class MessageParser:
    """Free text -> :class:`ParsedMessage`, against a fixed player universe."""

    def __init__(self, resolver: PlayerResolver, *, default_gw: int) -> None:
        self.resolver = resolver
        self.default_gw = int(default_gw)

    def parse(self, text: str) -> ParsedMessage:
        kind = classify(text)
        gw, explicit = extract_gw(text)
        groups = name_fragments(text)

        subject: Resolution | None = None
        rival: Resolution | None = None
        first_problem: Resolution | None = None
        used: set[int] = set()

        for group in groups:
            hit: Resolution | None = None
            for frag in group:
                res = self.resolver.resolve(frag)
                if res.ambiguous or not res.matched:
                    # Remember the first run we could not pin down, but keep
                    # trying its shorter alternatives: "b fernandes" is decisive
                    # even though "fernandes" alone is not.
                    if first_problem is None and res.candidates:
                        first_problem = res
                    continue
                best = res.best
                if best is None or int(best.code) in used:
                    continue
                hit = res
                break
            if hit is None:
                continue
            used.add(int(hit.candidates[0].code))
            if subject is None:
                subject = hit
            elif rival is None and kind in (IdeaKind.COMPARE, IdeaKind.CAPTAIN):
                rival = hit

        # Nothing resolved anywhere: surface the closest thing to a question we
        # have, so the user gets a shortlist rather than a shrug.
        if subject is None and first_problem is not None:
            subject = first_problem

        comparator = DEFAULT_COMPARATOR[kind]
        if rival is not None:
            comparator = Comparator.NAMED_PLAYER
        elif kind is IdeaKind.COMPARE:
            # "X or Y" where only X resolved: fall back to the class yardstick
            # rather than inventing an opponent.
            comparator = Comparator.PRICE_PEER_MEDIAN

        return ParsedMessage(
            raw_text=text,
            kind=kind,
            subject=subject,
            rival=rival,
            gw=GwId(gw) if gw else None,
            gw_was_explicit=explicit,
            horizon_gws=DEFAULT_HORIZON[kind],
            comparator=comparator,
            name_fragments=tuple(g[0] for g in groups),
        )


def interpret_reply(text: str, candidates: tuple[CandidateMatch, ...]) -> CandidateMatch | None:
    """Read a reply to a clarification prompt.

    Accepts the offered ordinal ("2") or a name that now resolves unambiguously
    within the offered set. Anything else returns None and is treated as a fresh
    message, so a user who ignores the question and types something new is not
    silently answering the old one.
    """
    stripped = text.strip()
    if stripped.isdigit():
        idx = int(stripped)
        if 1 <= idx <= len(candidates):
            return candidates[idx - 1]
        return None
    folded = _fold(stripped)
    if not folded:
        return None
    hits = [c for c in candidates if folded in _fold(c.label)]
    if len(hits) == 1:
        return hits[0]
    qtokens = _tokens(folded)
    scored = [
        (max(PlayerResolver._score_one(folded, qtokens, _fold(part)) for part in c.label.replace("(", " ").replace(")", " ").split()), c)
        for c in candidates
    ]
    scored.sort(key=lambda sc: -sc[0])
    if scored and scored[0][0] >= 0.9 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= AMBIGUITY_MARGIN):
        return scored[0][1]
    return None


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)
