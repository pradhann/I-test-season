"""User configuration.

Identifiers live here and are committed. Secrets live in ``.env``, which is
gitignored, and are read at runtime only. Nothing in this module should ever
contain a token.
"""

from __future__ import annotations

import os
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
        # Balanced: chase top 10k as the primary target with a meaningful
        # penalty on blowing up, and treat top 1k as the stretch.
        default_factory=lambda: RankUtilityConfig(
            target_rank=10_000, stretch_rank=1_000, risk_lambda=0.35
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
