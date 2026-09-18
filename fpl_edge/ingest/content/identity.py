"""Linking a creator name to an FPL entry, and refusing to guess when it cannot.

Moved out of ``fpl_edge/interfaces/creators.py`` (ARCHITECTURE_REVIEW.md check
6, move M3). ``fpl_edge/ingest/content/pipeline.py`` writes the
``creator_entry`` table from :func:`link_creator_entries`, and while this code
lived in ``interfaces`` that import was the third edge of cycle C2.

The rule these functions implement is narrow on purpose. A creator name,
accent- and case-folded by ``ingest.rivals.names.norm``, must equal exactly a
name the FPL API itself reported for one entry. A resemblance is not evidence,
and an FPL entry id rots every August into a different real person, so a
guessed link would be a fabrication written down as an identity.

Imports reach sideways into ``ingest.rivals`` and no higher, so nothing here
can close C2 again.
"""

from __future__ import annotations

from dataclasses import dataclass

#: What a pasted item is filed under when no creator could be resolved.
UNRESOLVED_CREATOR = "unresolved (pasted link)"


#: ``dim_manager.source`` prefixes whose ``player_name`` came back from the
#: FPL API rather than from a curated list.
#:
#: ``elite_named`` rows are written only after :func:`fpl_edge.ingest.rivals.
#: elite.verify` has checked the curated name against ``/entry/{id}/``;
#: ``expert`` rows are overwritten by ``_profile_row`` from the same endpoint;
#: ``top1k``, ``mini_league`` and ``snowball`` rows carry
#: ``player_first_name``/``player_last_name`` straight out of league standings.
#:
#: ``elite_list`` and ``winner`` are deliberately ABSENT. Those names are
#: pinned third-party text next to an ID nobody re-checked, and FPL entry IDs
#: rot every August into a different real person. Linking a creator to one of
#: those would be recording a guess as an identity.
API_NAMED_MANAGER_SOURCES = ("elite_named", "expert", "top1k", "mini_league",
                             "snowball")


@dataclass(frozen=True)
class CreatorEntry:
    """A creator resolved to an FPL entry, or the reason they were not."""

    creator: str
    entry_id: int | None
    player_name: str | None
    entry_name: str | None
    method: str
    verified: bool
    reason: str


def _person_shaped(normalised: str) -> bool:
    """At least a forename and a surname.

    A single token is not an identity: dozens of managers are called "Tom",
    and a creator called "Tom" matching all of them is not a link, it is a
    collision waiting to be written down as a fact.
    """
    return len(normalised.split()) >= 2


def verified_manager_index(wh) -> dict[str, list[dict]]:
    """Normalised API-sourced manager name -> the entries carrying that name.

    Built through :func:`fpl_edge.ingest.rivals.names.norm`, the one matcher,
    so a creator is checked against exactly the same folding that verifies a
    curated elite ID.
    """
    from fpl_edge.ingest.rivals.names import norm

    placeholders = ", ".join("?" for _ in API_NAMED_MANAGER_SOURCES)
    rows = wh.sql(
        "SELECT DISTINCT entry_id, player_name, entry_name, source FROM dim_manager "
        f"WHERE player_name IS NOT NULL AND player_name <> '' "
        f"AND split_part(source, ':', 1) IN ({placeholders})",
        list(API_NAMED_MANAGER_SOURCES),
    )
    index: dict[str, list[dict]] = {}
    for row in rows.itertuples(index=False):
        index.setdefault(norm(row.player_name), []).append({
            "entry_id": int(row.entry_id), "player_name": row.player_name,
            "entry_name": row.entry_name, "source": row.source,
        })
    return index


def link_creator_entries(wh, creators: list[str] | None = None) -> list[CreatorEntry]:
    """Link creators to FPL entries where the evidence already exists.

    The rule is deliberately narrow, and narrow is the point:

    * the creator's name, accent- and case-folded by ``names.norm``, must be
      EXACTLY a name the FPL API returned for an entry -- not a containment,
      not a fuzzy score. "Let's Talk FPL" does not become Andy's entry because
      "Andy LTFPL" looks like it might be the same person; that is a nickname
      resemblance, and the brief for this table is that a resemblance is not
      evidence;
    * the name must have at least two tokens (see :func:`_person_shaped`);
    * the name must be unambiguous -- one entry, not forty.

    Everything else comes back unresolved WITH a reason, which is what the
    panel renders. Unresolved is the expected majority: creator names are
    channel names, and a channel is not a person.
    """
    from fpl_edge.ingest.rivals.elite import ELITE_NAMED
    from fpl_edge.ingest.rivals.names import norm

    if creators is None:
        creators = sorted({
            c for c in wh.sql("SELECT DISTINCT creator FROM content_source").creator
            if c and c not in ("user-shared", UNRESOLVED_CREATOR)
        })
    index = verified_manager_index(wh)
    # The 8 hand-verified elite entries, keyed the same way. Their IDs were
    # checked against /entry/{id}/ by elite.verify, which is the strongest
    # provenance in the warehouse, so they are preferred when both agree.
    elite = {norm(e.name): e for e in ELITE_NAMED}

    out: list[CreatorEntry] = []
    for creator in creators:
        key = norm(creator)
        if not _person_shaped(key):
            out.append(CreatorEntry(creator, None, None, None, "none", False,
                                    "creator name is a single token; too "
                                    "ambiguous to identify a person"))
            continue
        hits = index.get(key, [])
        entry_ids = {h["entry_id"] for h in hits}
        if not entry_ids:
            out.append(CreatorEntry(
                creator, None, None, None, "none", False,
                "no FPL entry whose API-reported name equals this creator name"))
            continue
        if len(entry_ids) > 1:
            out.append(CreatorEntry(
                creator, None, None, None, "none", False,
                f"ambiguous: {len(entry_ids)} entries report this exact name"))
            continue
        hit = hits[0]
        named = elite.get(key)
        method = ("elite_named exact name match" if named is not None
                  else f"dim_manager({hit['source']}) exact name match")
        if named is not None and named.entry_id != hit["entry_id"]:
            out.append(CreatorEntry(
                creator, None, None, None, "none", False,
                f"conflict: ELITE_NAMED says {named.entry_id}, dim_manager says "
                f"{hit['entry_id']}; not guessing between them"))
            continue
        out.append(CreatorEntry(
            creator, hit["entry_id"], hit["player_name"], hit["entry_name"],
            method, True, "",
        ))
    return out
