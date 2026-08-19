"""News and tactical intel: what changed, when it became public, and who says so.

The package answers one question the rest of the engine cannot: *what does a
human know about this player that the numbers do not yet reflect?* That covers
injuries and availability, press-conference coverage, set-piece and penalty duty,
formation changes, and players FPL classifies in one position who play in
another.

Three commitments run through all of it.

**Every item is dated by when the world could have known, not by when we
noticed.** :class:`~fpl_edge.intel.items.IntelItem` carries ``published_at`` and
``observed_at`` separately and refuses to be constructed with the second before
the first. Reads filter on ``published_at``. This is what makes intel safe to
put in a backtest, and it is the reason the FPL API's ``news_added`` is the
injury feed here rather than a scraped table: a scrape has no publication
timestamp, so the scraper's clock silently becomes the injury's clock.

**Sources are reached honestly or not at all.** :mod:`fpl_edge.intel.sources`
fetches robots.txt first, treats an unreadable policy as a refusal, and never
forges a User-Agent or a TLS fingerprint to get past bot detection. When a site
says no, that is recorded as a dated measurement and reported to the user
instead of the section quietly disappearing.

**Nothing is asserted that can be measured.** The out-of-position detector scores
players against the empirical distribution of their own league rather than
against thresholds from a blog post; the set-piece valuation states its goals-
per-game assumption in the open; and every collector returns the count of what it
had to skip alongside what it wrote.
"""

from fpl_edge.intel.collect import CollectionReport, collect, collect_changes_only
from fpl_edge.intel.items import (
    Duty,
    DutyChange,
    FormationObservation,
    IntelItem,
    IntelKind,
    OopSignal,
    SetPieceDuty,
    SourceProbe,
    value_duty_change,
)
from fpl_edge.intel.store import IntelStore

__all__ = [
    "CollectionReport",
    "Duty",
    "DutyChange",
    "FormationObservation",
    "IntelItem",
    "IntelKind",
    "IntelStore",
    "OopSignal",
    "SetPieceDuty",
    "SourceProbe",
    "collect",
    "collect_changes_only",
    "value_duty_change",
]
