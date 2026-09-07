"""One honest STATE per source, so nothing working is reported as excluded.

The Creators panel used to divide sources into "shows on the panel" and
"excluded", and called everything in the second bucket excluded. On
2026-09-03 that list held 25 names, 39 of the 41 registered sources had been
probed that same day with HTTP 200, and several of the "excluded" ones had
landed items inside the hour. The word was wrong, and it was wrong in the
expensive direction: it told the owner that working feeds were switched off.

Three genuinely different things were being conflated, and this module keeps
them apart:

* **Permission.** X/Twitter is ``FORBIDDEN`` and r/FantasyPL is
  ``OAUTH_ONLY``; neither is fetched, by policy, and no probe will ever
  change that. :data:`SourceState.BLOCKED`.
* **Capability.** A creator we want with no verified feed URL is registered
  ``enabled=False`` with the reason recorded, rather than being given an
  invented URL or quietly left out. :data:`SourceState.DISABLED`.
* **Result.** Everything else is a measurement: when did we last reach it,
  what did it answer, and has it published anything. LIVE / QUIET / STALE /
  EMPTY / FAILING / UNPROBED, each defined below against a column.

And one thing that is not a source at all: ``user_link``, the pseudo-key the
paste-a-link path writes its items under (creator ``user-shared`` on the
older rows). It is a real row in ``content_item`` and never a row in
``content_source``, so it is reported as :data:`SourceState.LEGACY` instead
of being counted as a feed that stopped working.

Every field here is read from a column. Nothing is inferred from the shape of
the registry, because the registry is exactly what was being trusted when the
panel started calling live feeds excluded.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
from dataclasses import dataclass
from typing import Any

from fpl_edge.ingest.content.sources import (
    ALL_SOURCES,
    BY_KEY,
    AccessPolicy,
    Source,
    content_tier,
)

UTC = dt.UTC

#: How recent a probe, or a published item, has to be to count as "recently".
#: Two days: the fast tier runs every 4h and the full ingest daily, so a
#: source untouched for two days has genuinely been missed rather than merely
#: not being due.
DEFAULT_WINDOW_DAYS = 2

#: The pseudo-source keys the corpus carries that are not feeds. Items land
#: here from POST /api/ingest/link -- the owner pasting one URL -- so the key
#: is a record of provenance, not a subscription.
LEGACY_KEYS: frozenset[str] = frozenset({"user_link"})

#: Creator names that only ever appear on legacy pseudo-source rows. Kept
#: because the owner asked about this one by name ("user-shared is legacy
#: name"): a creator column carrying it is provenance, not a show.
LEGACY_CREATORS: frozenset[str] = frozenset({"user-shared"})


class SourceState(enum.StrEnum):
    """What is true of one source right now. Exactly one applies."""

    #: Probed inside the window, and it has published inside the window.
    LIVE = "live"
    #: Probed inside the window and answering, but nothing new published.
    #: This is the state that was being reported as "excluded" -- a healthy
    #: feed whose creator simply has not posted.
    QUIET = "quiet"
    #: Has items, but nothing has probed it inside the window. The ingest is
    #: behind on this source; this is the only result state that is a defect.
    STALE = "stale"
    #: Reached, but has never yielded a single item, ever.
    EMPTY = "empty"
    #: The last probe did not answer 200, or recorded an error.
    FAILING = "failing"
    #: Registered and fetchable, but nothing has ever probed it.
    UNPROBED = "unprobed"
    #: Registered with no verified URL. Never fetched. See ``disabled_reason``.
    DISABLED = "disabled"
    #: Fetching is refused on policy, not on capability.
    BLOCKED = "blocked"
    #: Not a subscription: items the owner pasted in by hand.
    LEGACY = "legacy"


#: Short labels for the panel. The state name is the contract; this is copy.
STATE_LABEL: dict[str, str] = {
    SourceState.LIVE: "fetching, new items",
    SourceState.QUIET: "fetching, nothing new",
    SourceState.STALE: "not fetched recently",
    SourceState.EMPTY: "reached, never any items",
    SourceState.FAILING: "last fetch failed",
    SourceState.UNPROBED: "never fetched",
    SourceState.DISABLED: "no verified feed",
    SourceState.BLOCKED: "blocked by policy",
    SourceState.LEGACY: "pasted by hand, not a feed",
}

#: States in which a manual "fetch latest" is worth offering. BLOCKED and
#: DISABLED are not: pressing the button could only fail, and the panel
#: saying why beats a button that lies.
FETCHABLE_STATES: frozenset[str] = frozenset({
    SourceState.LIVE, SourceState.QUIET, SourceState.STALE,
    SourceState.EMPTY, SourceState.FAILING, SourceState.UNPROBED,
})


@dataclass(frozen=True)
class SourceHealth:
    """One row of the source table, every field measured or NULL."""

    key: str
    creator: str
    kind: str
    url: str | None
    policy: str
    tier: str | None
    enabled: bool
    state: str
    reason: str
    can_fetch_now: bool
    last_probe_utc: str | None = None
    last_http_status: int | None = None
    last_items: int | None = None
    last_error: str | None = None
    n_items: int = 0
    n_items_window: int = 0
    last_item_utc: str | None = None
    last_fetched_utc: str | None = None
    n_claims_window: int = 0

    @property
    def label(self) -> str:
        return STATE_LABEL.get(self.state, self.state)

    def to_dict(self) -> dict[str, Any]:
        out = dataclasses.asdict(self)
        out["label"] = self.label
        return out


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        import pandas as pd

        ts = pd.to_datetime(value, utc=True, errors="coerce")
        return None if pd.isna(ts) else ts.isoformat()
    except Exception:  # noqa: BLE001 - a formatting failure is not a state
        return None


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        import pandas as pd

        return None if pd.isna(value) else int(value)
    except Exception:  # noqa: BLE001
        return None


def _has(wh, table: str) -> bool:
    try:
        return bool(int(wh.sql(
            "SELECT count(*) c FROM information_schema.tables "
            "WHERE table_name = ?", [table]).iloc[0]["c"]))
    except Exception:  # noqa: BLE001 - an unreadable catalogue is "absent"
        return False


def _classify(
    source: Source,
    probe: dict[str, Any],
    items: dict[str, Any],
    cutoff: dt.datetime,
) -> tuple[str, str]:
    """(state, reason) for one registered source. Pure; every branch cites a
    column so the panel can show the evidence next to the word."""
    if source.policy is not AccessPolicy.OPEN:
        return SourceState.BLOCKED, (
            source.note
            or f"access policy is {source.policy}; this source is never fetched")
    if not source.enabled:
        return SourceState.DISABLED, (
            source.disabled_reason
            or "registered with no verified feed URL, so it is never fetched")

    probed_at = probe.get("last_probe_utc")
    status = probe.get("last_http_status")
    error = probe.get("last_error")
    n_items = int(items.get("n_items") or 0)
    last_item = items.get("last_item_utc")
    last_fetch = items.get("last_fetched_utc")
    # "When did anything actually reach this source" is the later of the two
    # timestamps that record it: the probe column, and the newest fetched_at
    # on its items. An ingest run writes items without touching last_probe_utc,
    # so trusting the probe column alone would call a source that landed an
    # item an hour ago STALE.
    touched = max([t for t in (probed_at, last_fetch) if t is not None],
                  default=None)

    if error or (status is not None and int(status) != 200):
        return SourceState.FAILING, (
            f"last probe answered {status if status is not None else 'nothing'}"
            + (f": {error}" if error else ""))
    if touched is None:
        return SourceState.UNPROBED, (
            "no probe recorded and no item ever fetched from this source")
    if n_items == 0:
        return SourceState.EMPTY, (
            f"reached (last status {status}), and no item has ever been "
            f"stored from it")
    if touched < cutoff:
        return SourceState.STALE, (
            f"nothing has fetched this source since {_iso(touched)}")
    if last_item is not None and last_item >= cutoff:
        return SourceState.LIVE, (
            f"fetched {_iso(touched)}; newest item published {_iso(last_item)}")
    return SourceState.QUIET, (
        f"fetched {_iso(touched)} and answering; newest item published "
        f"{_iso(last_item)}, which is outside the window")


def source_states(
    wh,
    *,
    now: dt.datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[SourceHealth]:
    """Every registered source plus every legacy pseudo-source, with state.

    ``wh`` is any read handle (the panel scripts' ``read_copy`` is the
    intended caller). Missing tables are treated as absent evidence, never as
    an exception: a warehouse with no ``content_item`` yet yields a registry
    whose every open source is UNPROBED, which is true.
    """
    now = (now or dt.datetime.now(UTC)).astimezone(UTC)
    cutoff = now - dt.timedelta(days=window_days)

    probes: dict[str, dict[str, Any]] = {}
    if _has(wh, "content_source"):
        for row in wh.sql(
            "SELECT source_key, last_probe_utc, last_http_status, last_items, "
            "last_error FROM content_source"
        ).to_dict("records"):
            probes[str(row["source_key"])] = row

    items: dict[str, dict[str, Any]] = {}
    if _has(wh, "content_item"):
        for row in wh.sql(
            "SELECT source_key, any_value(creator) creator, any_value(kind) kind, "
            "       count(*) n_items, "
            "       sum(CASE WHEN published_at >= ? THEN 1 ELSE 0 END) n_window, "
            "       max(published_at) last_item_utc, "
            "       max(fetched_at) last_fetched_utc "
            "FROM content_item GROUP BY source_key", [cutoff]
        ).to_dict("records"):
            items[str(row["source_key"])] = row

    claims: dict[str, int] = {}
    if _has(wh, "content_claim"):
        for row in wh.sql(
            "SELECT source_key, count(*) n FROM content_claim "
            "WHERE published_at >= ? GROUP BY source_key", [cutoff]
        ).to_dict("records"):
            claims[str(row["source_key"])] = int(row["n"])

    out: list[SourceHealth] = []
    for source in ALL_SOURCES:
        probe = probes.get(source.key, {})
        item = items.get(source.key, {})
        state, reason = _classify(source, probe, item, cutoff)
        out.append(SourceHealth(
            key=source.key,
            creator=source.creator,
            kind=str(source.kind),
            url=source.url,
            policy=str(source.policy),
            tier=content_tier(source) if source.policy is AccessPolicy.OPEN else None,
            enabled=source.enabled,
            state=str(state),
            reason=reason,
            can_fetch_now=state in FETCHABLE_STATES,
            last_probe_utc=_iso(probe.get("last_probe_utc")),
            last_http_status=_int(probe.get("last_http_status")),
            last_items=_int(probe.get("last_items")),
            last_error=(str(probe["last_error"])
                        if probe.get("last_error") else None),
            n_items=int(item.get("n_items") or 0),
            n_items_window=int(item.get("n_window") or 0),
            last_item_utc=_iso(item.get("last_item_utc")),
            last_fetched_utc=_iso(item.get("last_fetched_utc")),
            n_claims_window=int(claims.get(source.key, 0)),
        ))

    # The pseudo-sources: keys in the corpus that the registry does not know.
    # They are reported, not hidden -- 9 items in this warehouse arrived that
    # way -- but they are reported as what they are.
    for key, item in sorted(items.items()):
        if key in BY_KEY:
            continue
        creator = str(item.get("creator") or key)
        out.append(SourceHealth(
            key=key,
            creator=creator,
            kind=str(item.get("kind") or "link"),
            url=None,
            policy="n/a",
            tier=None,
            enabled=False,
            state=str(SourceState.LEGACY),
            reason=(
                f"{item.get('n_items') or 0} item(s) the owner pasted in by "
                f"hand (POST /api/ingest/link). Not a feed, nothing polls it, "
                f"and it is not an excluded source."
                + (" 'user-shared' is the older label for the same thing."
                   if creator in LEGACY_CREATORS else "")
            ),
            can_fetch_now=False,
            n_items=int(item.get("n_items") or 0),
            n_items_window=int(item.get("n_window") or 0),
            last_item_utc=_iso(item.get("last_item_utc")),
            last_fetched_utc=_iso(item.get("last_fetched_utc")),
            n_claims_window=int(claims.get(key, 0)),
        ))

    order = list(SourceState)
    out.sort(key=lambda h: (order.index(SourceState(h.state)), h.creator, h.key))
    return out


def state_counts(rows: list[SourceHealth]) -> dict[str, int]:
    """``{state: n}`` over every state, zeros included, in declaration order."""
    counts = {str(s): 0 for s in SourceState}
    for row in rows:
        counts[row.state] = counts.get(row.state, 0) + 1
    return counts
