"""People, not shows.

``content_item.creator`` is a SHOW: "The FPL Wire", "Let's Talk FPL". A show
does not have an FPL team, does not make a pick and cannot have a track record.
Three people do, and the newest FPL Wire episode in the warehouse is titled
"Free Hit or Wildcard? - Zophar Gameweek 2 Team" -- one host's team, stored
under a three-host label. Every consensus count built at the show grain treats
that as the show's opinion, which is wrong in the direction that matters: it
inflates agreement between people who never agreed.

This module is the person layer. It owns three things and refuses a fourth.

**It owns the roster.** :func:`load_panel` reads a curated YAML file and
:func:`upsert_panel` writes ``panel_person`` and ``panel_person_show``. The file
is curated by a human because the contents are claims about real people --
their FPL entry id above all -- and none of it is derivable from the corpus.

**It owns attribution.** :func:`attribute_items` writes ``item_person`` rows,
each carrying the ``basis`` on which the attribution was made and the verbatim
``evidence`` that justified it. Two bases are computed here:

* ``sole_host`` -- the show has exactly one active person. Structural.
* ``title`` -- the episode title names them, whole-word, against an alias the
  panel file states explicitly, and only against aliases of people who are
  actually on that show. "Harry" in an FPL Focal title is not FPL Harry.

Two more are accepted from elsewhere and never invented here: ``stated`` (the
transcript says so -- the ASR/analysis side writes those) and ``manual``.

**It refuses to guess.** An item whose host cannot be established gets NO
``item_person`` row. That is not a gap to be filled later with a default; it is
the answer. A round-table episode with three hosts genuinely belongs to the
show, and :func:`person_claims_visible_at` LEFT JOINs so those claims keep
flowing through with ``person_key = None``. There is no "probably Zophar".

**Scope.** :func:`panel_scope` answers "which sources does the owner actually
care about" from the panel itself, so a run can spend itself on ~9 people
instead of 38 sources. Nothing is deleted: the scope is a filter over the same
registry, it degrades to "everything" with a stated reason when the panel is
absent, and every other source is one ``--only`` away.

POINT IN TIME
-------------
Nothing here opens a second read path for claims.
:meth:`~fpl_edge.ingest.content.store.ContentStore.claims_visible_at` remains
the sanctioned one, and :func:`person_claims_visible_at` is a thin join ON TOP
of its already-filtered result rather than a query of its own. An attribution
carries no publication instant and must never be used as one: whose episode it
was does not change when it was published, and ``published_at`` is still the
only column any reader filters on.

THE FILE
--------
``data/panels/creator_panel_2026_27.yaml``, shaped like this. Every field
except ``person_key``, ``display_name`` and ``shows`` is optional; an absent
file is not an error (see :attr:`Panel.missing_reason`)::

    season: "2026-27"
    as_of: 2026-08-27T00:00:00Z
    people:
      - person_key: zophar
        display_name: Zophar
        aliases: [Zophar, Zophar666]
        handles:
          twitter: zopharfpl
        entry_id: 1234567          # omit when unknown -- do not guess
        entry_verified: true
        entry_source_url: https://...   # where the id was confirmed
        entry_api_name: "Zophar's XI"   # what the FPL API calls that entry
        entry_checked_utc: 2026-08-26T10:00:00Z
        entry_reason: null         # REQUIRED when entry_id is absent
        top10k_finishes: 8
        edge_note: "differential-led, early transfers"
        active: true
        shows:
          - creator: The FPL Wire
            role: host
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from fpl_edge.ingest.content.sources import ALL_SOURCES, BY_KEY, Scope

UTC = dt.UTC

DEFAULT_PANEL_PATH = Path("data/panels/creator_panel_2026_27.yaml")

#: How an attribution was established. Closed set: a basis outside it is a
#: guess wearing a label.
BASIS_SOLE_HOST = "sole_host"
BASIS_TITLE = "title"
BASIS_STATED = "stated"
BASIS_MANUAL = "manual"
BASES: tuple[str, ...] = (BASIS_SOLE_HOST, BASIS_TITLE, BASIS_STATED, BASIS_MANUAL)

#: Confidence in the ATTRIBUTION -- whose episode this is -- and not in the
#: pick. ``sole_host`` is 1.0 because it is structural: there is nobody else it
#: could be. ``title`` is high but not certain: a title can name a guest, or
#: name someone being discussed rather than speaking.
BASIS_CONFIDENCE: dict[str, float] = {
    BASIS_SOLE_HOST: 1.0,
    BASIS_TITLE: 0.9,
    BASIS_STATED: 0.95,
    BASIS_MANUAL: 1.0,
}

#: An alias shorter than this is refused. Two-character aliases match inside
#: ordinary words often enough that one of them would eventually attribute a
#: hundred episodes to the wrong person, and nothing downstream would notice.
MIN_ALIAS_CHARS = 3

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def _as_utc(value: Any, label: str) -> dt.datetime | None:
    """Parse a YAML timestamp to aware UTC, or None. Never guesses an offset."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, dt.date):
        # A bare date in YAML is midnight UTC of that date, which is what the
        # writer meant by "checked on the 26th". Stated, not inferred.
        parsed = dt.datetime(value.year, value.month, value.day, tzinfo=UTC)
    else:
        try:
            parsed = dt.datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise ValueError(f"{label}: unparsable timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class PersonShow:
    """One person on one show. ``source_key`` NULL means all of its sources."""

    show_creator: str
    source_key: str | None = None
    role: str | None = None


@dataclass(frozen=True, slots=True)
class PanelPerson:
    person_key: str
    display_name: str
    aliases: tuple[str, ...]
    handles: dict[str, str]
    shows: tuple[PersonShow, ...]
    entry_id: int | None = None
    entry_verified: bool = False
    entry_source_url: str | None = None
    entry_api_name: str | None = None
    entry_checked_utc: dt.datetime | None = None
    entry_reason: str | None = None
    edge_note: str | None = None
    top10k_finishes: int | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class Panel:
    """The roster, plus everything that was wrong with the file.

    ``problems`` is not a log. A person who fails validation is NOT in
    ``people`` -- they are here instead, with the reason -- so a malformed
    entry cannot reach the warehouse, and a silent partial load cannot be
    mistaken for a clean one.
    """

    people: tuple[PanelPerson, ...] = ()
    season: str | None = None
    as_of: dt.datetime | None = None
    #: 'yaml' when the file stated it, 'file_mtime' when it did not and the
    #: file's own modification time stood in. Recorded because those are
    #: different facts and the second is weaker.
    as_of_basis: str | None = None
    path: Path | None = None
    #: Set when the file is not there. Not an error: the panel is produced by a
    #: separate process and every caller here degrades to show-level behaviour.
    missing_reason: str | None = None
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.missing_reason is None and not self.problems

    def render(self) -> str:
        if self.missing_reason:
            return f"panel: absent ({self.missing_reason})"
        lines = [
            (
                f"panel: {len(self.people)} people, season={self.season}, "
                f"as_of={self.as_of.isoformat() if self.as_of else None} "
                f"({self.as_of_basis})"
            )
        ]
        for person in self.people:
            entry = (
                f"entry={person.entry_id}"
                f"{'' if person.entry_verified else ' UNVERIFIED'}"
                if person.entry_id is not None
                else f"entry=NULL ({person.entry_reason})"
            )
            shows = ", ".join(s.show_creator for s in person.shows)
            lines.append(f"  {person.person_key:>16}  {entry:<28}  {shows}")
        for problem in self.problems:
            lines.append(f"  REJECTED: {problem}")
        return "\n".join(lines)


def _known_creators() -> set[str]:
    return {s.creator for s in ALL_SOURCES}


def _parse_person(raw: Any, index: int, problems: list[str]) -> PanelPerson | None:
    """One YAML mapping -> a person, or None with the reason recorded.

    The validation rules exist because each of them is a way to fabricate:

    * An ``entry_id`` with no ``entry_source_url`` behind a ``verified: true``
      is an unsourced claim about someone's real FPL account.
    * A missing ``entry_id`` with no ``entry_reason`` is a blank that later
      reads as "nobody looked", when it may mean "looked and it is not public".
      A null gets a reason; that is the rule for the whole package.
    * A show that is not in the registry cannot be joined to any item, so
      accepting it would create a mapping that silently matches nothing.
    """
    where = f"people[{index}]"
    if not isinstance(raw, dict):
        problems.append(f"{where}: not a mapping")
        return None
    key = str(raw.get("person_key") or "").strip()
    if not _SLUG_RE.match(key):
        problems.append(f"{where}: person_key {key!r} must be lowercase [a-z0-9_]")
        return None
    where = f"person {key!r}"
    display = str(raw.get("display_name") or "").strip()
    if not display:
        problems.append(f"{where}: display_name is required")
        return None

    entry_raw = raw.get("entry_id")
    entry_id: int | None = None
    if entry_raw not in (None, "", "null"):
        try:
            entry_id = int(entry_raw)
        except (TypeError, ValueError):
            problems.append(f"{where}: entry_id {entry_raw!r} is not an integer")
            return None
        if entry_id <= 0:
            problems.append(f"{where}: entry_id {entry_id} is not a real entry")
            return None
    entry_verified = bool(raw.get("entry_verified", False))
    entry_source_url = (raw.get("entry_source_url") or None)
    entry_reason = (raw.get("entry_reason") or None)
    if entry_id is None and not entry_reason:
        problems.append(
            f"{where}: entry_id is absent and entry_reason is empty. A null needs a "
            f"reason -- 'never stated publicly', 'stated but unconfirmed', "
            f"'not looked for yet' are all fine; a blank is not."
        )
        return None
    if entry_id is not None and entry_verified and not entry_source_url:
        problems.append(
            f"{where}: entry_verified is true but entry_source_url is empty. "
            f"Verified against what?"
        )
        return None
    if entry_id is None and entry_verified:
        problems.append(f"{where}: entry_verified is true with no entry_id")
        return None

    try:
        checked = _as_utc(raw.get("entry_checked_utc"), f"{where}.entry_checked_utc")
    except ValueError as exc:
        problems.append(str(exc))
        return None

    aliases_raw = raw.get("aliases") or [display]
    aliases = tuple(
        dict.fromkeys(
            a.strip() for a in (str(x) for x in aliases_raw)
            if len(a.strip()) >= MIN_ALIAS_CHARS
        )
    )
    dropped = [
        str(x) for x in aliases_raw if len(str(x).strip()) < MIN_ALIAS_CHARS
    ]
    if dropped:
        problems.append(
            f"{where}: aliases {dropped} are shorter than {MIN_ALIAS_CHARS} "
            f"characters and were NOT loaded; they would match inside words"
        )

    handles = raw.get("handles") or {}
    if not isinstance(handles, dict):
        problems.append(f"{where}: handles must be a mapping")
        return None

    shows: list[PersonShow] = []
    known = _known_creators()
    for entry in raw.get("shows") or []:
        if isinstance(entry, str):
            entry = {"creator": entry}
        if not isinstance(entry, dict):
            problems.append(f"{where}: show entry {entry!r} is not a mapping")
            continue
        source_key = (entry.get("source_key") or None)
        creator = (entry.get("creator") or entry.get("show_creator") or None)
        if source_key is not None:
            source = BY_KEY.get(str(source_key))
            if source is None:
                problems.append(
                    f"{where}: source_key {source_key!r} is not in the registry"
                )
                continue
            creator = creator or source.creator
        if not creator:
            problems.append(f"{where}: show entry has neither creator nor source_key")
            continue
        if str(creator) not in known:
            problems.append(
                f"{where}: show {creator!r} is not a registered creator, so it "
                f"could never join to a content_item row"
            )
            continue
        shows.append(
            PersonShow(
                show_creator=str(creator),
                source_key=str(source_key) if source_key else None,
                role=str(entry.get("role")) if entry.get("role") else None,
            )
        )
    if not shows:
        problems.append(f"{where}: no valid shows, so nothing could be attributed")
        return None

    top10k = raw.get("top10k_finishes")
    return PanelPerson(
        person_key=key,
        display_name=display,
        aliases=aliases or (display,),
        handles={str(k): str(v) for k, v in handles.items()},
        shows=tuple(shows),
        entry_id=entry_id,
        entry_verified=entry_verified,
        entry_source_url=str(entry_source_url) if entry_source_url else None,
        entry_api_name=str(raw["entry_api_name"]) if raw.get("entry_api_name") else None,
        entry_checked_utc=checked,
        entry_reason=str(entry_reason) if entry_reason else None,
        edge_note=str(raw["edge_note"]) if raw.get("edge_note") else None,
        top10k_finishes=int(top10k) if top10k not in (None, "") else None,
        active=bool(raw.get("active", True)),
    )


def load_panel(path: Path | str = DEFAULT_PANEL_PATH) -> Panel:
    """Read the curated panel file. An absent file is a Panel, not an exception.

    The file is produced by a separate process. Everything downstream of here
    has a defined behaviour when it does not exist -- attribution writes
    nothing, the scope degrades to every source -- so raising would convert a
    known, handled state into an outage.
    """
    path = Path(path)
    if not path.exists():
        return Panel(missing_reason=f"no such file: {path}", path=path)
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        return Panel(missing_reason=f"unparsable YAML in {path}: {exc}", path=path)
    # Two shapes are accepted, because two people wrote to this file
    # independently and both spellings are natural: a mapping with a `people:`
    # key (which can also carry `as_of`), or a bare list of people, which is
    # how a roster reads when it is nothing but a roster. Rejecting the second
    # would have been a contract argument, not a data problem.
    if isinstance(raw, list):
        raw = {"people": raw}
    if not isinstance(raw, dict):
        return Panel(
            missing_reason=(
                f"{path} is neither a mapping nor a list of people "
                f"(got {type(raw).__name__})"
            ),
            path=path,
        )

    problems: list[str] = []
    try:
        as_of = _as_utc(raw.get("as_of"), "as_of")
    except ValueError as exc:
        problems.append(str(exc))
        as_of = None
    if as_of is None:
        # The file's own mtime is an observed fact about when the roster was
        # last edited. Weaker than a curated stamp, so which one was used is
        # recorded rather than smoothed over.
        as_of = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        as_of_basis = "file_mtime"
    else:
        as_of_basis = "yaml"

    people: list[PanelPerson] = []
    seen: set[str] = set()
    for index, raw_person in enumerate(raw.get("people") or []):
        person = _parse_person(raw_person, index, problems)
        if person is None:
            continue
        if person.person_key in seen:
            problems.append(f"person {person.person_key!r}: duplicate person_key")
            continue
        seen.add(person.person_key)
        people.append(person)

    return Panel(
        people=tuple(people),
        season=str(raw["season"]) if raw.get("season") else None,
        as_of=as_of,
        as_of_basis=as_of_basis,
        path=path,
        problems=tuple(problems),
    )


@dataclass(frozen=True, slots=True)
class PanelWrite:
    people_written: int = 0
    shows_written: int = 0
    skipped_reason: str | None = None


def upsert_panel(warehouse, panel: Panel) -> PanelWrite:
    """Replace the stored roster with this one.

    Delete-then-insert, scoped to the whole table, and that is the right
    semantics HERE even though it is the wrong semantics for every other table
    in this package. content_item and content_claim are archives of things that
    were published; the panel is a current editorial statement about who is on
    which show. A person who leaves a show must lose the mapping, and an
    insert-only upsert would leave them attributed to it forever.

    Nothing derived is destroyed: ``item_person`` rows are keyed on item and
    person and survive untouched, so re-loading the panel does not discard
    attribution work.
    """
    if panel.missing_reason:
        return PanelWrite(skipped_reason=panel.missing_reason)
    if not panel.people:
        return PanelWrite(skipped_reason="panel has no valid people")
    as_of = panel.as_of or _now()

    person_rows = [
        {
            "person_key": p.person_key,
            "display_name": p.display_name,
            "handles_json": json.dumps(p.handles, sort_keys=True) if p.handles else None,
            "aliases_json": json.dumps(list(p.aliases)),
            "entry_id": p.entry_id,
            "entry_verified": bool(p.entry_verified),
            "entry_source_url": p.entry_source_url,
            "entry_api_name": p.entry_api_name,
            "entry_checked_utc": p.entry_checked_utc,
            "entry_reason": p.entry_reason,
            "edge_note": p.edge_note,
            "top10k_finishes": p.top10k_finishes,
            "active": bool(p.active),
            "as_of": as_of,
        }
        for p in panel.people
    ]
    show_rows = [
        {
            "person_key": p.person_key,
            "show_creator": s.show_creator,
            "source_key": s.source_key,
            "role": s.role,
            "as_of": as_of,
        }
        for p in panel.people
        for s in p.shows
    ]
    people = pd.DataFrame(person_rows)
    shows = pd.DataFrame(show_rows).drop_duplicates(
        subset=["person_key", "show_creator"]
    )
    warehouse._con.register("_incoming_people", people)
    warehouse._con.register("_incoming_shows", shows)
    try:
        warehouse.sql("BEGIN TRANSACTION")
        try:
            warehouse.sql("DELETE FROM panel_person_show")
            warehouse.sql("DELETE FROM panel_person")
            warehouse.sql(
                f"INSERT INTO panel_person ({', '.join(people.columns)}) "
                f"SELECT {', '.join(people.columns)} FROM _incoming_people"
            )
            warehouse.sql(
                f"INSERT INTO panel_person_show ({', '.join(shows.columns)}) "
                f"SELECT {', '.join(shows.columns)} FROM _incoming_shows"
            )
            warehouse.sql("COMMIT")
        except Exception:
            # A half-written roster would attribute items to people whose
            # show mapping had been deleted.
            warehouse.sql("ROLLBACK")
            raise
    finally:
        warehouse._con.unregister("_incoming_people")
        warehouse._con.unregister("_incoming_shows")
    return PanelWrite(people_written=len(people), shows_written=len(shows))


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def panel_scope(warehouse) -> Scope:
    """The sources the panel's people actually publish through.

    "No need to fetch everything." This is that, as a value rather than as a
    deletion: the registry keeps all 38 sources, and a run that passes this
    scope simply does not spend a backfill on the 29 nobody asked about.

    Degrades UPWARD, never to silence. No panel tables, no rows, or a panel
    whose shows match no registered source all return
    :meth:`Scope.everything` with a label saying which of those it was --
    because a scope that quietly selected zero sources is indistinguishable
    from a fetcher that is broken.
    """
    try:
        rows = warehouse.sql(
            "SELECT s.show_creator, s.source_key FROM panel_person_show s "
            "JOIN panel_person p USING (person_key) WHERE p.active"
        )
    except Exception:  # noqa: BLE001 -- table may not exist yet
        return Scope.everything("panel tables not present; scope not narrowed")
    if rows.empty:
        return Scope.everything("panel is empty; scope not narrowed")

    keys: set[str] = set()
    creators: set[str] = set()
    for _, row in rows.iterrows():
        source_key = row["source_key"]
        if isinstance(source_key, str) and source_key:
            keys.add(source_key)
        else:
            creators.add(str(row["show_creator"]))
    keys.update(s.key for s in ALL_SOURCES if s.creator in creators)
    if not keys:
        return Scope.everything(
            "panel shows match no registered source; scope not narrowed"
        )
    return Scope.from_keys(
        sorted(keys),
        label=f"panel: {len(creators) or len(keys)} shows from panel_person_show",
    )


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttributionReport:
    items_considered: int = 0
    sole_host: int = 0
    title: int = 0
    written: int = 0
    #: Items in scope that gained no attribution at all. NOT a failure: a
    #: three-host round table belongs to the show, and this is the count of
    #: items for which that is the honest answer.
    unattributed: int = 0
    aliases_used: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def render(self) -> str:
        lines = [
            (
                f"attribution: {self.items_considered} items considered, "
                f"{self.sole_host} sole_host + {self.title} title = "
                f"{self.written} rows written"
            ),
            (
                f"             {self.unattributed} items left attributed to the "
                f"show (no person could be established -- a legitimate state)"
            ),
        ]
        lines.extend(f"             note: {n}" for n in self.notes)
        return "\n".join(lines)


def _alias_pattern(alias: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive, literal. No fuzzy matching, ever.

    ``\\b`` on both ends so "Pras" does not match "Prasanna" and "Tom" does not
    match "Tomorrow" -- the second of which is the realistic one for FPL
    episode titles.
    """
    return re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE)


def attribute_items(
    warehouse,
    *,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> AttributionReport:
    """Write ``item_person`` for the two bases that can be established here.

    What this does NOT do, in order of how tempting each was:

    * It does not attribute a multi-host episode to whoever is listed first.
    * It does not attribute by publication cadence ("Zophar always does
      Tuesdays"), which would be a model dressed as a fact.
    * It does not fall back to the show's most prolific host when the title
      names nobody.
    * It does not match aliases across shows: the join to
      ``panel_person_show`` is what stops "Harry" in an FPL Focal title from
      becoming FPL Harry.

    The residue -- items that match none of the above -- is counted and left
    alone, which is what :attr:`AttributionReport.unattributed` reports.
    """
    stamp = now or _now()
    people = warehouse.sql(
        "SELECT p.person_key, p.display_name, p.aliases_json, p.active, "
        "       s.show_creator, s.source_key "
        "FROM panel_person p JOIN panel_person_show s USING (person_key) "
        "WHERE p.active"
    )
    if people.empty:
        return AttributionReport(notes=("panel is empty; nothing attributed",))

    items = warehouse.sql(
        "SELECT item_id, creator, source_key, title FROM content_item"
    )
    if items.empty:
        return AttributionReport(notes=("no content items to attribute",))

    # A show maps to its people. `source_key` narrows a person to one feed of
    # that show when the panel says so; NULL means every feed of it.
    by_show: dict[str, list[dict[str, Any]]] = {}
    aliases_used: set[str] = set()
    for _, row in people.iterrows():
        try:
            aliases = [str(a) for a in json.loads(row["aliases_json"] or "[]")]
        except (TypeError, ValueError):
            aliases = []
        aliases = [a for a in aliases if len(a) >= MIN_ALIAS_CHARS]
        by_show.setdefault(str(row["show_creator"]), []).append(
            {
                "person_key": str(row["person_key"]),
                "source_key": (
                    str(row["source_key"])
                    if isinstance(row["source_key"], str) and row["source_key"]
                    else None
                ),
                "aliases": aliases,
            }
        )
        aliases_used.update(aliases)

    rows: list[dict[str, Any]] = []
    sole = 0
    titled = 0
    attributed_items: set[str] = set()
    considered = 0
    for _, item in items.iterrows():
        panel_people = by_show.get(str(item["creator"]))
        if not panel_people:
            continue
        eligible = [
            p for p in panel_people
            if p["source_key"] is None or p["source_key"] == str(item["source_key"])
        ]
        if not eligible:
            continue
        considered += 1
        item_id = str(item["item_id"])
        title = str(item["title"] or "")

        matched_any = False
        for person in eligible:
            for alias in person["aliases"]:
                match = _alias_pattern(alias).search(title)
                if match is None:
                    continue
                rows.append(
                    {
                        "item_id": item_id,
                        "person_key": person["person_key"],
                        "basis": BASIS_TITLE,
                        "confidence": BASIS_CONFIDENCE[BASIS_TITLE],
                        # The verbatim span, so a wrong attribution can be
                        # found by reading rather than by re-deriving.
                        "evidence": match.group(0),
                        "attributed_utc": stamp,
                    }
                )
                titled += 1
                matched_any = True
                break

        if len(eligible) == 1:
            rows.append(
                {
                    "item_id": item_id,
                    "person_key": eligible[0]["person_key"],
                    "basis": BASIS_SOLE_HOST,
                    "confidence": BASIS_CONFIDENCE[BASIS_SOLE_HOST],
                    # Structural: there is no quotable evidence, and inventing
                    # a quote for it would be the only dishonest thing here.
                    "evidence": None,
                    "attributed_utc": stamp,
                }
            )
            sole += 1
            matched_any = True

        if matched_any:
            attributed_items.add(item_id)

    written = 0
    if rows and not dry_run:
        frame = pd.DataFrame(rows).drop_duplicates(
            subset=["item_id", "person_key", "basis"]
        )
        warehouse._con.register("_incoming_attrib", frame)
        try:
            before = int(warehouse.sql(
                "SELECT count(*) c FROM item_person"
            ).iloc[0]["c"])
            cols = ", ".join(frame.columns)
            # Insert-once on the primary key. An attribution already recorded
            # -- possibly by hand, possibly by the transcript side with better
            # evidence than a title match -- is not overwritten by this pass.
            warehouse.sql(
                f"INSERT INTO item_person ({cols}) SELECT {cols} FROM _incoming_attrib i "
                f"WHERE NOT EXISTS (SELECT 1 FROM item_person t "
                f"  WHERE t.item_id = i.item_id AND t.person_key = i.person_key "
                f"    AND t.basis = i.basis)"
            )
            after = int(warehouse.sql(
                "SELECT count(*) c FROM item_person"
            ).iloc[0]["c"])
            written = after - before
        finally:
            warehouse._con.unregister("_incoming_attrib")
    elif rows:
        written = len(
            pd.DataFrame(rows).drop_duplicates(
                subset=["item_id", "person_key", "basis"]
            )
        )

    return AttributionReport(
        items_considered=considered,
        sole_host=sole,
        title=titled,
        written=written,
        unattributed=considered - len(attributed_items),
        aliases_used=tuple(sorted(aliases_used)),
        notes=("dry run: nothing written",) if dry_run else (),
    )


# ---------------------------------------------------------------------------
# The read
# ---------------------------------------------------------------------------


def person_claims_visible_at(
    store,
    as_of: dt.datetime,
    *,
    season: str | None = None,
    gameweek: int | None = None,
    person_key: str | None = None,
) -> pd.DataFrame:
    """Claims visible at ``as_of``, carrying the person they belong to.

    A join on top of :meth:`ContentStore.claims_visible_at`, never a query of
    its own. That is the whole design: there is exactly one place that filters
    ``published_at < as_of``, and adding a person column must not become a
    second one. If this function grew its own SELECT over content_claim, the
    leakage guard would have to be re-proved here, and the next such function
    would not bother.

    LEFT JOIN, deliberately. An unattributed claim keeps flowing with
    ``person_key = None``: it is still the show's claim and still counts for
    the show. Dropping it would make the person layer quietly delete the
    majority of the corpus.

    Deduplicated to one row per (claim, person). Two bases can hold at once and
    each is a separate ``item_person`` row; letting both through would
    double-count that claim in any consensus built on this, which is exactly
    the failure the person layer exists to fix.
    """
    claims = store.claims_visible_at(as_of, season=season, gameweek=gameweek)
    try:
        attribution = store.wh.sql(
            "SELECT item_id, person_key, basis, confidence AS attribution_confidence "
            "FROM item_person"
        )
    except Exception:  # noqa: BLE001 -- table may not exist yet
        attribution = pd.DataFrame(
            columns=["item_id", "person_key", "basis", "attribution_confidence"]
        )
    if not attribution.empty:
        attribution = (
            attribution.sort_values("attribution_confidence", ascending=False)
            .drop_duplicates(subset=["item_id", "person_key"])
        )
    if claims.empty:
        for column in ("person_key", "basis", "attribution_confidence"):
            claims[column] = pd.Series(dtype="object")
        return claims
    merged = claims.merge(attribution, on="item_id", how="left")
    # An unmatched left join yields NaN, and NaN is not what "this item belongs
    # to nobody in particular" means. Callers check `is None` (and `if not
    # row.person_key`, which NaN passes as TRUE); handing them a float would
    # make the unattributed state read as an attribution to a person named nan.
    for column in ("person_key", "basis"):
        merged[column] = merged[column].astype(object).where(
            merged[column].notna(), None
        )
    if person_key is not None:
        merged = merged[merged["person_key"] == person_key]
    return merged.reset_index(drop=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_load(args: argparse.Namespace) -> int:
    from fpl_edge.ingest.content.store import ContentStore
    from fpl_edge.store import Warehouse

    panel = load_panel(args.file)
    print(panel.render())
    if panel.missing_reason:
        return 0 if args.allow_missing else 1
    with Warehouse(args.db) as warehouse:
        applied = ContentStore(warehouse).migrate()
        if applied:
            print(f"migrations applied: {', '.join(applied)}")
        if args.dry_run:
            print("dry run: nothing written")
            return 0
        write = upsert_panel(warehouse, panel)
        if write.skipped_reason:
            print(f"not written: {write.skipped_reason}")
            return 1
        print(
            f"wrote {write.people_written} panel_person rows, "
            f"{write.shows_written} panel_person_show rows"
        )
    return 0


def _cmd_scope(args: argparse.Namespace) -> int:
    from fpl_edge.store import Warehouse

    with Warehouse(args.db, read_only=True) as warehouse:
        scope = panel_scope(warehouse)
    if args.keys_only:
        selected = scope.apply()
        print(",".join(s.key for s in selected))
        return 0
    print(scope.render())
    for source in scope.apply():
        print(f"  {source.key:>22}  {source.creator}")
    return 0


def _cmd_attribute(args: argparse.Namespace) -> int:
    from fpl_edge.ingest.content.store import ContentStore
    from fpl_edge.store import Warehouse

    with Warehouse(args.db) as warehouse:
        ContentStore(warehouse).migrate()
        report = attribute_items(warehouse, dry_run=args.dry_run)
        print(report.render())
    return 0


def main(argv: list[str] | None = None) -> int:
    """::

        uv run python -m fpl_edge.ingest.content.panel load
        uv run python -m fpl_edge.ingest.content.panel attribute --dry-run
        uv run python -m fpl_edge.ingest.content.panel scope --keys-only

    The last one prints a comma-separated source list suitable for the existing
    ``pipeline.py ingest --only``, which is how the panel becomes the default
    ingest target without this module reaching into a file another team owns.
    """
    parser = argparse.ArgumentParser(
        prog="python -m fpl_edge.ingest.content.panel",
        description="The person layer: roster, attribution, and panel scope.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    load = sub.add_parser("load", help="read the panel YAML and upsert the roster")
    load.add_argument("--db", default="data/warehouse/fpl.duckdb")
    load.add_argument("--file", default=str(DEFAULT_PANEL_PATH))
    load.add_argument("--dry-run", action="store_true")
    load.add_argument(
        "--allow-missing", action="store_true",
        help="exit 0 when the panel file does not exist yet",
    )
    load.set_defaults(func=_cmd_load)

    scope = sub.add_parser("scope", help="which sources the panel's people publish through")
    scope.add_argument("--db", default="data/warehouse/fpl.duckdb")
    scope.add_argument(
        "--keys-only", action="store_true",
        help="print just the comma-separated keys, for `ingest --only`",
    )
    scope.set_defaults(func=_cmd_scope)

    attribute = sub.add_parser(
        "attribute", help="write item_person for sole_host and title bases"
    )
    attribute.add_argument("--db", default="data/warehouse/fpl.duckdb")
    attribute.add_argument("--dry-run", action="store_true")
    attribute.set_defaults(func=_cmd_attribute)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
