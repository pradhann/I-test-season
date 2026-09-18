"""projection_table: the player board, joined to live price and ownership.

Two data regimes live behind one registered name:

* **Artefact mode** (the original, still the default): the solved simulation
  parquet frozen at solve time, joined to live price/ownership. The dashboard
  calls this with ``{limit, sort}`` and must keep rendering unchanged.
* **Gameweek mode** (``gw`` or any gw-only param present): the third-party
  provider projections in ``projection_normalized``, read through the semantic
  layer (``sem_projections`` / ``sem_projection_consensus``). ``source="all"``
  (or omitted) gives the consensus per player with the min-max SPREAD as a
  first-class column: source disagreement is the uncertainty estimate. A
  specific ``source`` gives that vendor's raw numbers. ``detail_code`` adds a
  per-source breakdown for one player over the chosen GW and the next four.

``weighting`` picks which consensus the gameweek mode serves: ``"equal"``
(the default; ``sem_projection_consensus``) or ``"earned"`` (the inverse-MSE
weights the calibration loop fitted, through
``sem_projection_consensus_weighted``). The two are never blended: the payload
names which one drove every row-bearing block, and the earned view travels
with the weights table (weight, n_obs, MAE, baseline MAE, fitted-at) so the
reader can see WHY a provider is down-weighted. The default stays equal on
purpose: three settled gameweeks is a thin track record.

``p_appear`` is deliberately a separate column from ``xpts`` and is never
multiplied in: "3.1 xPts" and "82% to appear" are different claims about
different random variables, and the rank layer needs them separate
(FPLForm's design, kept on purpose).
"""

from __future__ import annotations

from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.projections.artefact import _artefact_mode
from fpl_edge.platform.scripts.projections.gw import _gw_mode
from fpl_edge.platform.scripts.projections.schema import (  # noqa: F401
    _ARTEFACT_RESULT,
    _GW_RESULT,
    PARAMS,
    RESULT,
)

#: Every name a caller outside this package reads off `scripts.projections`:
#:   _ARTEFACT_RESULT  tests/unit/test_projection_gw_mode.py:33
#:   _GW_RESULT        tests/unit/test_projection_gw_mode.py:33
#:   RESULT            tests/unit/test_projection_gw_mode.py:33
#: PARAMS is the panel's other public constant. `projection_table` is the
#: dispatcher itself and is defined below.
__all__ = ["PARAMS", "RESULT", "projection_table"]


def projection_table(
    wh,
    *,
    season: str,
    position: int | None = None,
    sort: str = "xpts",
    limit: int = 50,
    max_price: float | None = None,
    gw: int | str | None = None,
    source: str | None = None,
    team: str | None = None,
    min_p_appear: float | None = None,
    detail_code: int | None = None,
    span: int = 5,
    sources: list[str] | None = None,
    weighting: str = "equal",
) -> dict[str, Any]:
    """Projected points per player: solved artefact by default, or per-gameweek
    provider consensus (with the cross-source spread as the uncertainty column)
    when ``gw``/``source``/``team``/``min_p_appear``/``detail_code`` is given.

    Returns empty when the requested regime has no data, naming what does
    exist: the artefact branch says to run the solve, the gameweek branch
    lists which gameweeks the ingested sources actually cover.
    """
    gw_mode = any(p is not None for p in (gw, source, team, min_p_appear,
                                          detail_code, sources))
    if gw_mode:
        return _gw_mode(
            wh, season=season, position=position, sort=sort, limit=limit,
            max_price=max_price, gw=gw, source=source, team=team,
            min_p_appear=min_p_appear, detail_code=detail_code, span=span,
            subset=sources, weighting=weighting,
        )
    return _artefact_mode(
        wh, season=season, position=position, sort=sort, limit=limit,
        max_price=max_price,
    )


register_script(
    "projection_table",
    projection_table,
    params_schema=PARAMS,
    result_schema=RESULT,
    title="Projection table",
    description="Projected points per player: the solved artefact by default, "
                "or per-gameweek provider consensus with the cross-source "
                "spread when a gameweek is chosen.",
)
