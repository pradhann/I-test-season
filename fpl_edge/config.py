"""User configuration.

Identifiers live here and are committed. Secrets live in ``.env``, which is
gitignored, and are read at runtime only. Nothing in this module should ever
contain a token.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from fpl_edge.models.contracts import RankUtilityConfig

ENV_PATH = Path(".env")


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    """Minimal .env reader. No dependency, no surprises, no logging of values."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def secret(name: str, *, required: bool = True) -> str | None:
    """Read a secret from the environment, falling back to .env.

    Raises rather than returning an empty string when a required secret is
    missing, so a misconfigured deployment fails at startup instead of silently
    doing nothing at the deadline.
    """
    value = os.environ.get(name) or load_env().get(name) or ""
    if not value:
        if required:
            raise RuntimeError(
                f"{name} is not set. Add it to .env (which is gitignored) "
                f"or export it. Never commit it."
            )
        return None
    return value


@dataclass(frozen=True)
class MiniLeague:
    league_id: int
    name: str
    kind: str  # "classic" | "h2h"
    private: bool


#: Global/auto-entered leagues. Not rivals -- useful only as reference fields.
GLOBAL_LEAGUES: tuple[MiniLeague, ...] = (
    MiniLeague(314, "Overall", "classic", private=False),
    MiniLeague(322, "Top 10% 25/26 League", "classic", private=False),
    MiniLeague(16, "Man Utd", "classic", private=False),
    MiniLeague(170, "Nepal", "classic", private=False),
    MiniLeague(276, "Gameweek 1", "classic", private=False),
)

#: The leagues actually worth optimising against in mini-league mode.
MINI_LEAGUES: tuple[MiniLeague, ...] = (
    MiniLeague(76109, "NRs.20 to Enter", "classic", private=True),
    MiniLeague(76254, "NRs.20 to Enter", "h2h", private=True),
    MiniLeague(82939, "Viva Les Blues", "classic", private=True),
    MiniLeague(1264466, "Chiya Pasal", "classic", private=True),
    MiniLeague(1264565, "Chiya Pasal Invitational", "h2h", private=True),
)


@dataclass(frozen=True)
class UserConfig:
    """Everything the engine needs to know about whose team it is optimising."""

    entry_id: int = 4490171
    team_name: str = "i-test"
    season: str = "2026-27"

    #: Supported club. Tracked because club affinity is a known source of
    #: selection bias, and the idea inbox measures it rather than assuming it.
    supported_club_league_id: int = 16
    supported_club: str = "Man Utd"

    #: Realised overall ranks, from /api/entry/{id}/history/. Recorded because
    #: the honest prior on this manager is high variance around a mediocre
    #: median, not steady excellence, and the engine should not be tuned as if
    #: the 2018/19 result were the base rate.
    #:
    #:   2016/17 2,080,652   2017/18 1,969,101   2018/19     9,524
    #:   2019/20    89,399   2021/22 3,508,427   2022/23 1,529,396
    #:   2024/25   176,269   2025/26 1,005,916
    #:
    #: Median ~1.27M, best 9,524, worst 3.51M. The 2025/26 finish qualifies for
    #: the Top 10% league only because the field was ~11M, i.e. roughly the 9th
    #: percentile rather than comfortably inside it.
    past_ranks: tuple[tuple[str, int], ...] = (
        ("2016/17", 2_080_652), ("2017/18", 1_969_101), ("2018/19", 9_524),
        ("2019/20", 89_399), ("2021/22", 3_508_427), ("2022/23", 1_529_396),
        ("2024/25", 176_269), ("2025/26", 1_005_916),
    )

    rank_utility: RankUtilityConfig = field(
        # The stated objective is to WIN: P(rank = 1) is the objective the user
        # set in the refined brief (2026-08-18), with top-1k as the fallback
        # measure of progress. This supersedes the earlier "Balanced" answer.
        #
        # Encoded honestly: rank 1 of ~11M is not a target an optimizer can
        # steer to directly -- P(rank=1) for any single-season plan is so small
        # that its Monte Carlo estimate is pure noise at any feasible n_sims.
        # The operational objective is therefore P(top 1k), which is the
        # direction rank-1 lives in, with P(rank=1) REPORTED alongside it and
        # risk_lambda dropped low: going for the win means accepting a fat left
        # tail, and the config should not quietly hedge that away.
        default_factory=lambda: RankUtilityConfig(
            target_rank=1_000, stretch_rank=1, risk_lambda=0.10
        )
    )

    mini_leagues: tuple[MiniLeague, ...] = MINI_LEAGUES
    global_leagues: tuple[MiniLeague, ...] = GLOBAL_LEAGUES

    @property
    def median_past_rank(self) -> int:
        import statistics

        return int(statistics.median(r for _, r in self.past_ranks))

    @property
    def best_past_rank(self) -> int:
        return min(r for _, r in self.past_ranks)

    @property
    def telegram_token(self) -> str:
        return secret("TELEGRAM_BOT_TOKEN")  # type: ignore[return-value]

    @property
    def telegram_allowed_chat_id(self) -> int | None:
        raw = secret("TELEGRAM_ALLOWED_CHAT_ID", required=False)
        return int(raw) if raw else None


USER = UserConfig()


# ---------------------------------------------------------------- model pins

#: A bare Anthropic model id: ``claude-`` followed by hyphen-separated
#: lowercase alphanumeric segments (``claude-opus-5``, ``claude-sonnet-5``,
#: ``claude-opus-4-5-20251101``). Nothing else. Backend labels, plan names and
#: session ids are not model ids and never pass this.
MODEL_ID_RE = re.compile(r"^claude-[a-z0-9]+(?:-[a-z0-9]+)*$")


def _model_pin(env_name: str, default: str) -> str:
    """One model id, from the environment or the committed default.

    Not a secret, so it goes through the same ``.env`` reader without the
    secret machinery. An id that is not a bare model id fails here, at import,
    rather than after a night of runs stamped with something unjoinable.
    """
    value = (os.environ.get(env_name) or load_env().get(env_name) or "").strip()
    chosen = value or default
    if not MODEL_ID_RE.match(chosen):
        raise RuntimeError(
            f"{env_name}={chosen!r} is not a bare Anthropic model id such as "
            f"{default!r}. Aliases ('opus'), plan names and session ids do not "
            f"belong in a model pin: the id is written into content_analysis "
            f"and into the fetch ledger, where it has to stay joinable."
        )
    return chosen


#: Claim extraction over stored transcripts and articles
#: (``fpl_edge/ingest/content/analyze.py``). The highest-volume spender in the
#: repo: 799 stored analyses, one model call each. Sonnet because the work is
#: structured extraction against a JSON schema over text the model is handed
#: in full, which is the workload Sonnet is priced for; the prompt does the
#: judgement, not the tier.
ANALYSIS_MODEL = _model_pin("FPL_EDGE_ANALYSIS_MODEL", "claude-sonnet-5")

#: The briefing salience pass (``fpl_edge/platform/briefing_intel.py``): one
#: shot, no tools, ranking items that panels already computed. Same shape as
#: the analysis pass, so the same tier.
BRIEFING_MODEL = _model_pin("FPL_EDGE_BRIEFING_MODEL", "claude-sonnet-5")

#: The chat agent (``fpl_edge/platform/chat_agent.py``): an interactive,
#: tool-using agent over the whole toolbelt, a handful of turns a day. Opus,
#: because this is the surface the owner reasons with and it is the one place
#: where the per-turn cost is dwarfed by the cost of a wrong answer. Was
#: ``model="opus"``, a moving alias; pinned to the id that alias resolved to.
CHAT_MODEL = _model_pin("FPL_EDGE_CHAT_MODEL", "claude-opus-5")


# ------------------------------------------------------------- owner entry id

#: The deployment variable that names the owner's FPL team. Listed in
#: railway.toml and DEPLOYMENT.md, and until now read by nothing: the id was
#: the literal on ``UserConfig.entry_id``, so a deployment that set this
#: variable ran against the wrong team and said nothing about it.
OWNER_ENTRY_ID_ENV = "FPL_ENTRY_ID"


def owner_entry_id() -> int:
    """The owner's FPL entry id: ``FPL_ENTRY_ID`` if set, else ``USER``.

    A function rather than a constant because the environment can change
    between import and the first request (the boot sequence reads the volume,
    a test sets the variable), and because one function is greppable. Its
    callers are ``fpl_edge/platform/users.py``, which is the only module that
    decides whose team a request is about, and the price radar's squad filter
    in ``fpl_edge/pipelines/tasks.py``, which is an owner-run task and must
    not import the platform layer to learn one number.

    A value that is not a positive integer raises here rather than being
    ignored. Falling back to the committed default would run the whole engine
    against the wrong team and print the right-looking number everywhere.
    """
    raw = (os.environ.get(OWNER_ENTRY_ID_ENV)
           or load_env().get(OWNER_ENTRY_ID_ENV) or "").strip()
    if not raw:
        return int(USER.entry_id)
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"{OWNER_ENTRY_ID_ENV}={raw!r} is not an FPL entry id. The id is "
            f"the number in the URL when the manager views their own points "
            f"page."
        ) from exc
    if value <= 0:
        raise RuntimeError(
            f"{OWNER_ENTRY_ID_ENV}={raw!r} is not a positive integer, so no "
            f"FPL team has it."
        )
    return value


def owner_team_name() -> str | None:
    """The owner's FPL team name, which is a label and never an identifier.

    Beside :func:`owner_entry_id` so the two facts the owner context carries
    are read from one place. The squad card prints this; nothing joins on it,
    which is why a configuration without one is None rather than an error.
    """
    name = getattr(USER, "team_name", None)
    return str(name) if name else None
