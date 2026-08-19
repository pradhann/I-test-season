"""Oracle verdicts for the upcoming gameweek from every wired source."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from fpl_edge.eval.calibration import skill_score
from fpl_edge.models.minutes import GBMMinutesModel, TrainingSetBuilder
from fpl_edge.models.points.model import DecomposedPointsModel
from fpl_edge.models.points.shares import estimate_rates
from fpl_edge.models.team_goals import DixonColesModel
from fpl_edge.oracle.adapters import (
    from_anytime_scorer_odds,
    from_ownership_differential,
    from_points_model,
)
from fpl_edge.oracle.signals import SourceKind, SourceWeight, aggregate
from fpl_edge.store import Warehouse

SEASON = "2026-27"
HISTORY = ["2022-23", "2023-24", "2024-25", "2025-26"]

# Measured on real walk-forward data by the team-goals evaluation:
# Dixon-Coles log loss 0.98184 against the last-season-table baseline 1.03778.
DC_SKILL = skill_score(0.98184, 1.03778)


def build_weights() -> dict[str, SourceWeight]:
    """Weights, each traceable to a measurement or explicitly unmeasured."""
    return {
        "decomposed_points": SourceWeight.from_skill_score(
            "decomposed_points", SourceKind.OWN_MODEL, skill=DC_SKILL, sample=1140,
        ),
        # The market beat our goal model out of sample, so it is weighted at
        # least as highly. Sample is the number of walk-forward fixtures.
        "bookmaker_consensus": SourceWeight.from_skill_score(
            "bookmaker_consensus", SourceKind.MARKET, skill=DC_SKILL * 1.2, sample=1067,
        ),
        # Differential value is a definition, not a prediction, but its payoff
        # is unproven until the rank-utility simulator is verified.
        "differential_value": SourceWeight(
            source="differential_value", kind=SourceKind.OWNERSHIP,
            hit_rate=None, sample=0,
        ),
    }


def scorer_odds(wh: Warehouse, players: pd.DataFrame) -> pd.DataFrame:
    """De-vigged anytime-scorer probabilities matched to FPL codes."""
    raw = wh.sql(
        "SELECT selection, min(price_decimal) AS price FROM fact_odds "
        "WHERE market = 'anytime_scorer' GROUP BY selection"
    )
    if raw.empty:
        return raw
    # normalize_name handles what NFKD alone cannot: stroke letters like the
    # one in Ødegaard have no decomposition, and a plain accent-strip deletes
    # them, so "Odegaard" never matched. It is also the same folding the rest
    # of the codebase uses, so one fix propagates everywhere.
    from fpl_edge.ingest.player_mapping import normalize_name as fold

    known_codes = set(players["code"].astype(int))

    lookup: dict[str, int] = {}
    surname_counts: dict[str, int] = {}
    for code, web, first, second in zip(
        players["code"], players["web_name"],
        players.get("first_name", players["web_name"]),
        players.get("second_name", players["web_name"]),
    ):
        full = fold(f"{first} {second}")
        for key in {fold(web), full}:
            if key:
                lookup[key] = int(code)
        # Surnames are only usable when unambiguous across the league.
        surname = fold(second).split(" ")[-1] if second else ""
        if surname:
            surname_counts[surname] = surname_counts.get(surname, 0) + 1
            lookup.setdefault(f"sur:{surname}", int(code))

    sorted_lookup = {" ".join(sorted(k.split())): v for k, v in lookup.items()
                     if not k.startswith("sur:")}

    def resolve(sel: str) -> float:
        # Some feeds store an already-resolved FPL code as the selection.
        text = str(sel).strip()
        if text.isdigit() and int(text) in known_codes:
            return int(text)
        key = fold(sel)
        if key in lookup:
            return lookup[key]
        # "Magalhaes Gabriel" is "Gabriel Magalhães" surname-first; token order
        # carries no information for matching, so compare order-insensitively.
        sorted_key = " ".join(sorted(key.split()))
        if sorted_key in sorted_lookup:
            return sorted_lookup[sorted_key]
        # Bookmakers write "Viktor Gyokeres"; FPL may know him by a shorter
        # web_name. Try the last token as an unambiguous surname.
        last = key.split(" ")[-1] if key else ""
        if last and surname_counts.get(last) == 1 and f"sur:{last}" in lookup:
            return lookup[f"sur:{last}"]
        return float("nan")

    raw["code"] = raw["selection"].map(resolve)
    matched = raw.dropna(subset=["code"]).copy()
    matched["code"] = matched["code"].astype(int)
    # Two selections can resolve to one player -- a stored numeric code and a
    # name form. Keep the shortest price (highest implied probability) per code.
    matched = matched.sort_values("price").drop_duplicates("code", keep="first")
    # Two-way overround is not recoverable per selection here, so the raw
    # implied probability is used and the optimism is stated rather than hidden.
    matched["prob"] = 1.0 / matched["price"]
    print(f"  odds matched {len(matched)}/{len(raw)} selections to FPL codes")
    return matched[["code", "prob"]]


def main(n_sims: int = 1500) -> None:
    with Warehouse.read_copy() as wh:
        now = wh.snapshot_at(dt.datetime.now(dt.timezone.utc))
        deadline = now.deadline(SEASON, 1)
        snap = wh.snapshot_at(deadline)

        goals = DixonColesModel(); goals.fit(snap, SEASON)
        ts = TrainingSetBuilder(snapshot_at=wh.snapshot_at, catalog=snap).build(HISTORY)
        mins = GBMMinutesModel().fit(ts)
        rates = estimate_rates(snap, HISTORY)
        model = DecomposedPointsModel(goal_model=goals, minutes_model=mins, rates=rates)
        sample = model.simulate(snap, SEASON, 1, n_sims=n_sims, seed=20260821)

        players = snap.selectable(SEASON)
        signals = from_points_model(sample, players, as_of=deadline)
        signals += from_ownership_differential(players, sample, as_of=deadline)
        odds = scorer_odds(wh, players)
        signals += from_anytime_scorer_odds(odds, players, as_of=deadline)

        weights = build_weights()
        print("\nSOURCE WEIGHTS")
        for w in weights.values():
            print("  " + w.explain())

        verdicts = aggregate(signals, weights, as_of=deadline)
        name = dict(zip(players["code"], players["web_name"]))
        own = dict(zip(players["code"], players["selected_by_pct"]))
        price = dict(zip(players["code"], players["price_tenths"]))
        xpts = dict(zip(sample.codes, sample.mean()))

        rows = [{
            "player": name.get(c, c), "price": price.get(c, 0) / 10,
            "own%": own.get(c), "xpts": round(float(xpts.get(c, 0)), 2),
            "oracle": round(v.score, 3), "conf": round(v.confidence, 2),
            "sources": len(v.contributions),
        } for c, v in verdicts.items()]
        df = pd.DataFrame(rows).sort_values("oracle", ascending=False)
        print(f"\nTOP 15 BY ORACLE SCORE  ({len(signals)} signals over "
              f"{len(verdicts)} players)")
        print(df.head(15).to_string(index=False))

        print("\nWORKING SHOWN FOR THE TOP PICK")
        top = df.iloc[0]
        code = next(c for c, n in name.items() if n == top["player"])
        print(verdicts[code].explain(name=top["player"]))


if __name__ == "__main__":
    main()
