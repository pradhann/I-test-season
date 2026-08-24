"""The ``transfers`` section of the weekly report.

Registered through :func:`fpl_edge.interfaces.report.register_section`, so it
appears in ``make weekly`` without the report renderer knowing this package
exists.

The section follows the convention the report was built around: it says what it
cannot do rather than filling the space. There are three distinct ways this
section can have nothing to recommend, and they need different sentences,
because "we do not know your squad" and "we know your squad but have no points
model" are different problems with different fixes:

1. **No squad.** Pre-GW1, and nobody has entered the 15. The fix is one message
   to the bot, and the section says so.
2. **No points forecast.** The optimiser has no forecast to consume. The fix is
   fitting the points model; the section names the seam.
3. **No rank-utility provider.** The engine's actual objective is unavailable
   because the simulator has not shipped a provider. The section refuses to
   present an expected-points answer as if it were the rank answer, and says
   which one it is showing.

A provider is registered lazily: the section reaches for whatever forecast the
caller has configured and, finding none, reports the gap. It never constructs a
projection of its own.
"""

from __future__ import annotations

import datetime as dt
from typing import Callable

from fpl_edge.config import USER
from fpl_edge.interfaces.report import register_section
from fpl_edge.myteam.forecast import PointsForecastUnavailableError
from fpl_edge.myteam.recommend import NoSquadError, recommend
from fpl_edge.myteam.state import (
    NO_PICKS_BEFORE_KICKOFF,
    MyTeamState,
    PlayerIndex,
    Provenance,
    reconstruct,
)
from fpl_edge.myteam.store import MyTeamStore
from fpl_edge.opt import ObjectiveMode
from fpl_edge.opt.interfaces import RankUtilityUnavailableError
from fpl_edge.store import Warehouse

#: How many gameweeks the transfer recommendation looks ahead by default.
DEFAULT_HORIZON = 3

#: The forecast providers the section will use if something has configured them.
#: Left empty on purpose: this package does not fit models, and a section that
#: silently instantiated one would make ``make weekly`` take five minutes and
#: produce a recommendation from a model the reader never chose.
_PROVIDERS: dict[str, object] = {}


def configure(
    *,
    points_forecast: object | None = None,
    price_forecast: object | None = None,
    rank_utility: object | None = None,
    rank_mv: object | None = None,
    validator: object | None = None,
    mode: ObjectiveMode | None = None,
    horizon: int | None = None,
) -> None:
    """Give the weekly report the providers it needs to make a recommendation.

    Called by whoever owns the forecast lifecycle -- a scheduled job that fits
    the points model, or the CLI. Until it is called, the section reports the gap
    rather than inventing one.
    """
    for key, value in (
        ("points_forecast", points_forecast),
        ("price_forecast", price_forecast),
        ("rank_utility", rank_utility),
        ("rank_mv", rank_mv),
        ("validator", validator),
        ("mode", mode),
        ("horizon", horizon),
    ):
        if value is not None:
            _PROVIDERS[key] = value


def providers() -> dict[str, object]:
    return dict(_PROVIDERS)


def reset_providers() -> None:
    _PROVIDERS.clear()


def current_state(
    wh: Warehouse,
    season: str,
    as_of: dt.datetime,
    *,
    entry_id: int | None = None,
    gw: int | None = None,
    client: object | None = None,
    store: MyTeamStore | None = None,
) -> MyTeamState:
    """Reconstruct the manager's state, falling back to the manual squad."""
    entry_id = int(entry_id if entry_id is not None else USER.entry_id)
    snapshot = wh.snapshot_at(as_of)
    store = store or MyTeamStore(entry_id)
    manual = store.confirmed(season=season)
    from fpl_edge.myteam.sources import PublicEntryClient

    owned_client = client is None
    client = client or PublicEntryClient()
    try:
        private = None
        try:
            from fpl_edge.myteam.private import PrivateTeamClient, StaleSessionError
            from fpl_edge.myteam.tokens import TokenManager

            if not PrivateTeamClient.disabled_by_env():
                pc = PrivateTeamClient()
                if pc.configured or TokenManager().configured:
                    try:
                        private = pc.fetch(entry_id)
                    except StaleSessionError:
                        # Reported in the section body via provenance; a stale
                        # session must not take the whole weekly report down.
                        private = None
        except Exception:  # noqa: BLE001 - the report renders without it
            private = None
        return reconstruct(
            snapshot,
            entry_id=entry_id,
            season=season,
            client=client,          # type: ignore[arg-type]
            manual=manual,
            private=private,
            gw=gw,
        )
    finally:
        if owned_client:
            client.close()          # type: ignore[union-attr]


def render_transfers(
    wh: Warehouse, season: str, gw: int, as_of: dt.datetime
) -> str | None:
    """The ``transfers`` section. Never invents a recommendation."""
    head = "## Transfers"
    try:
        state = current_state(wh, season, as_of, gw=gw)
    except Exception as exc:  # noqa: BLE001 - a dead endpoint must not kill the report
        return (
            f"{head}\n\nCould not reconstruct your squad from the public FPL "
            f"endpoints: {type(exc).__name__}: {exc}\n\n"
            "No recommendation is offered. Guessing at the squad would make every "
            "line below it fiction."
        )

    snapshot = wh.snapshot_at(as_of)
    index = PlayerIndex.from_snapshot(snapshot, season)
    lines = [head, "", state.render(), ""]

    if state.picks is None:
        lines += [
            "**No transfer recommendation: your squad is not known.**",
            "",
            f"What is missing is {NO_PICKS_BEFORE_KICKOFF}.",
            "",
            "Tell me your 15 once and this becomes automatic from the moment GW1 "
            "kicks off:",
            "",
            "```",
            "fpl myteam set \"Raya, Gabriel, ... \"     # or paste one per line",
            "```",
            "",
            "or send the list to the bot and reply `confirm <token>`. Nothing is "
            "stored until you confirm it back.",
        ]
        return "\n".join(lines)

    if state.provenance is Provenance.MANUAL:
        lines += [
            "_Squad from your own manual entry, not from FPL. It will be "
            "reconciled against the public picks endpoint the moment the "
            "gameweek starts, and any disagreement reported rather than "
            "silently overwritten._",
            "",
        ]
    if state.failures:
        lines += [
            "**The reconstruction does not reconcile.** These are checks of a "
            "derived number against an observed one, and a failure means the "
            "reconstruction is wrong, not the game:",
            "",
        ]
        lines += [f"- {c.render()}" for c in state.failures]
        lines.append("")

    mode = _PROVIDERS.get("mode", ObjectiveMode.RANK_UTILITY)
    horizon = int(_PROVIDERS.get("horizon", DEFAULT_HORIZON))  # type: ignore[arg-type]
    gws = list(range(int(gw), int(gw) + horizon))
    try:
        rec = recommend(
            snapshot,
            state,
            season=season,
            gws=gws,
            points_forecast=_PROVIDERS.get("points_forecast"),
            price_forecast=_PROVIDERS.get("price_forecast"),
            mode=mode,                                   # type: ignore[arg-type]
            rank_utility=_PROVIDERS.get("rank_utility"),
            rank_mv=_PROVIDERS.get("rank_mv"),
            validator=_PROVIDERS.get("validator"),
        )
    except RankUtilityUnavailableError as exc:
        lines += [
            "**No transfer recommendation: the rank-utility objective has no "
            "provider.**",
            "",
            "This engine optimises a utility of final rank, not expected points. "
            "That objective is a functional of the joint distribution of your "
            "score against the field's, so it needs the simulator's "
            "`RankUtilityProvider`, which is not built yet.",
            "",
            "An expected-points answer is available and is a genuinely different "
            "recommendation -- it cannot see ownership, variance or covariance, so "
            "it systematically misprices every differential. Ask for it by name:",
            "",
            "```",
            f"fpl myteam transfers --mode expected-points --gw {gw}",
            "```",
            "",
            f"_{exc}_",
        ]
        return "\n".join(lines)
    except PointsForecastUnavailableError as exc:
        lines += [
            "**No transfer recommendation: no points forecast is configured.**",
            "",
            "The optimiser consumes a `PointsForecast` -- one row per (code, gw) "
            "with `xpts` and `p_play`. Nothing in this report fits models, and "
            "substituting a projection nobody chose would make the recommendation "
            "untraceable.",
            "",
            "Run `uv run fpl solve` -- it fits the models, solves the horizon "
            "and commits both the plan and the forecast table this section "
            "reads. (Programmatic callers can also "
            "`fpl_edge.myteam.report.configure(points_forecast=...)` directly.)",
            "",
            f"_{exc}_",
        ]
        return "\n".join(lines)
    except NoSquadError as exc:
        lines += ["**No transfer recommendation.**", "", f"_{exc}_"]
        return "\n".join(lines)

    lines += ["```", rec.render(index), "```"]
    return "\n".join(lines)


def register(render: Callable | None = None) -> None:
    """Attach the section to the weekly report.

    Called on import of :mod:`fpl_edge.myteam`, so anything that imports this
    package gets the section. Re-registering replaces, so this is idempotent.
    """
    register_section(
        "transfers",
        render or render_transfers,
        priority=40,
        provides="transfers",
    )
