"""The measured numbers that go on every ModelCard in this package.

Kept in one place so the cards cannot drift away from
``docs/models/minutes_eval.csv``; ``tests/unit/test_minutes_eval.py`` asserts
the two agree. Regenerate with::

    uv run python -m fpl_edge.models.minutes.evaluate \\
        --warehouse data/warehouse/fpl.duckdb --catalog-at 2026-08-19T00:00:00+00:00 \\
        --write-docs
    uv run python -m fpl_edge.models.minutes.evaluate --tag synthetic --write-docs

These are *committed benchmark* figures, not a property of whatever data a
particular instance was last fitted to. Calling ``fit`` does not change them.
"""

from __future__ import annotations

#: Held-out season of the reported fold, trained on everything before it.
TEST_SEASON = "2025-26"
TRAINED_THROUGH = "2024-25"
SLICE = "all"

#: Real Premier League seasons in the warehouse, walk-forward:
#: train 2022-23 + 2023-24 + 2024-25, test 2025-26, 29,747 player-fixtures.
DATASET = "warehouse seasons 2022-23..2025-26 (real FPL history)"

#: model -> (multiclass log loss, multiclass Brier) on the reported fold.
LOG_LOSS: dict[str, float] = {
    "base_rate": 0.9106,
    "prior_season": 0.7989,
    "fpl_chance": 0.9106,
    "hierarchical": 0.5215,
    "gbm": 0.4165,
}
BRIER: dict[str, float] = {
    "base_rate": 0.5401,
    "prior_season": 0.4624,
    "fpl_chance": 0.5401,
    "hierarchical": 0.2890,
    "gbm": 0.2340,
}

#: GW1-only slice of the same fold: the case that is live right now (n = 690).
COLD_LOG_LOSS: dict[str, float] = {
    "base_rate": 0.9472,
    "prior_season": 0.8021,
    "fpl_chance": 0.9472,
    "hierarchical": 0.7349,
    "gbm": 0.6330,
}

#: The FPL-chance baseline scores exactly the base rate because the historical
#: archive carries no availability at all: `status` and
#: `chance_of_playing_next_round` are NULL on 100% of pre-2026-27 state rows.
#: The baseline is therefore reported as measured and as uninformative on this
#: data, not as evidence that the field is worthless.
FPL_CHANCE_COVERAGE = 0.0


def notes(model: str, *, extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    return (
        f"walk-forward: train <= {TRAINED_THROUGH}, test {TEST_SEASON}, slice '{SLICE}'",
        f"Brier {BRIER[model]:.4f}; GW1 cold-start log loss {COLD_LOG_LOSS[model]:.4f}",
        f"measured on {DATASET}",
        *extra,
    )
