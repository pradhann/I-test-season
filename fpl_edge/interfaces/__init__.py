"""User-facing surfaces: the idea inbox, the Telegram bot, the weekly report.

Everything a human touches goes through :class:`~fpl_edge.interfaces.inbox.IdeaInbox`.
Telegram, the CLI and the MCP server are three renderers over one implementation
of "the user had a thought", so they cannot drift apart.

Imports here are kept to the pure-domain modules. :mod:`fpl_edge.interfaces.telegram`
is deliberately absent: importing it pulls in httpx and the bot machinery, and
``fpl idea review`` should not need either.
"""

from fpl_edge.interfaces.bias import BiasFinding, Review, Scoreboard, review
from fpl_edge.interfaces.ideas import (
    CandidateMatch,
    Clarification,
    Comparator,
    Idea,
    IdeaContext,
    IdeaKind,
    IdeaRecord,
    IdeaStatus,
    Outcome,
    Stance,
    Verdict,
)
from fpl_edge.interfaces.inbox import IdeaInbox, Submission
from fpl_edge.interfaces.parsing import MessageParser, PlayerResolver
from fpl_edge.interfaces.registry import IdeaRegistry
from fpl_edge.interfaces.report import register_section, weekly_report
from fpl_edge.interfaces.tracking import track
from fpl_edge.interfaces.verdict import (
    PriorVerdict,
    SimulationVerdict,
    TimeBounded,
    VerdictProvider,
    default_provider,
)

__all__ = [
    "BiasFinding",
    "CandidateMatch",
    "Clarification",
    "Comparator",
    "Idea",
    "IdeaContext",
    "IdeaInbox",
    "IdeaKind",
    "IdeaRecord",
    "IdeaRegistry",
    "IdeaStatus",
    "MessageParser",
    "Outcome",
    "PlayerResolver",
    "PriorVerdict",
    "Review",
    "Scoreboard",
    "SimulationVerdict",
    "Stance",
    "Submission",
    "TimeBounded",
    "Verdict",
    "VerdictProvider",
    "default_provider",
    "register_section",
    "review",
    "track",
    "weekly_report",
]
