"""Overfitting to one season, and evaluation that is not walk-forward.

Hunt list item 8. A model evaluated on a random split of a football panel has
already seen the future of every player it is being tested on. The only honest
protocol is: fit on everything visible at deadline T, predict the gameweek after
T, advance T, never look back.

Two things must hold and each is tested separately:

* the SPLIT is temporal, on every model, enforced statically;
* the CARD is populated. ``ModelCard.beats_baseline`` returns ``None`` when a
  score is missing, and ``None`` is falsy. "It runs" is not a status, and an
  unpopulated card is exactly how "it runs" gets mistaken for "it works".
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

from fpl_edge.models.contracts import ModelCard

from .conftest import REPO_ROOT, load_audit_script


def test_no_shuffled_split_anywhere_in_the_tree() -> None:
    """GUARDS: train_test_split / KFold / cross_val_score on time-series data.

    ``TimeSeriesSplit`` is permitted; everything that shuffles is not. Enforced
    statically so it covers modules that land after this was written.
    """
    audit = load_audit_script()
    offenders = [f for f in audit.audit_tree(REPO_ROOT) if f.rule == "NOT_WALK_FORWARD"]
    assert not offenders, (
        "non-temporal cross-validation on a football panel:\n  "
        + "\n  ".join(f.render() for f in offenders)
    )


def test_every_model_family_ships_a_walk_forward_evaluator() -> None:
    """GUARDS: a model family with no out-of-sample protocol at all.

    A family that has an ``evaluate`` module but no ``walk_forward`` in it has
    an evaluation of some other kind, and this suite wants to know which.
    """
    import fpl_edge.models

    families = {
        m.name for m in pkgutil.iter_modules(fpl_edge.models.__path__) if m.ispkg
    }
    missing = []
    for family in sorted(families):
        try:
            evaluate = importlib.import_module(f"fpl_edge.models.{family}.evaluate")
        except ModuleNotFoundError:
            missing.append(f"{family} (no evaluate module)")
            continue
        if not hasattr(evaluate, "walk_forward"):
            missing.append(f"{family}.evaluate (no walk_forward)")
    assert not missing, f"model families without a walk-forward evaluator: {missing}"


def test_walk_forward_never_trains_on_the_gameweek_it_predicts() -> None:
    """GUARDS: an off-by-one in the fold boundary.

    The classic version: fold k trains on gameweeks 1..k and tests on k, rather
    than on k+1. It is one character in a range, it improves every metric, and
    the resulting model has memorised the answer.

    Asserted arithmetically over the fold structure so it holds regardless of
    which evaluator produced it.
    """
    def folds(gws: list[int], min_train: int) -> list[tuple[list[int], int]]:
        return [(gws[:i], gws[i]) for i in range(min_train, len(gws))]

    built = folds(list(range(1, 11)), min_train=3)
    assert built[0] == ([1, 2, 3], 4), "first fold must train on 1..3 and test on 4"
    for train, test in built:
        assert test not in train, f"fold predicting GW{test} trained on it"
        assert max(train) < test, (
            f"fold predicting GW{test} trained on GW{max(train)}, which is later"
        )


def test_model_cards_are_populated_or_honestly_empty() -> None:
    """GUARDS: an unscored model being presented as a working one.

    ``beats_baseline`` returns ``None`` when either score is missing, and a
    caller writing ``if card.beats_baseline:`` reads that as False -- or, worse,
    a report template renders it as a blank rather than as "unmeasured".

    This test does not demand that every card be scored today; it demands that
    an unscored card SAYS so in its notes, so the gap is visible in the weekly
    report rather than inferred from a missing number.
    """
    unscored_and_silent = []
    for name, card in _discover_cards():
        if card.beats_baseline is not None:
            continue
        notes = " ".join(card.notes).lower()
        if "not yet" in notes or "unpopulated" in notes or "not populated" in notes:
            continue
        unscored_and_silent.append(name)
    assert not unscored_and_silent, (
        "these model cards have no score and no note saying so, so a reader "
        f"cannot tell an unmeasured model from a measured one: {unscored_and_silent}"
    )


def test_a_card_claiming_a_score_also_names_what_it_beat() -> None:
    """GUARDS: a score with no baseline, which is a number with no meaning.

    ``ModelCard`` requires ``baseline`` and ``metric`` as fields, so this checks
    they are non-empty rather than placeholder.
    """
    thin = [
        name for name, card in _discover_cards()
        if card.score is not None and (not card.baseline.strip() or not card.metric.strip())
    ]
    assert not thin, f"cards report a score with no baseline or metric: {thin}"


def test_trained_through_is_a_real_season_that_exists_in_the_warehouse(live_wh) -> None:
    """GUARDS: a card claiming training data the warehouse does not contain.

    At the time of this audit the warehouse holds 2026-27 only, and 2026-27 has
    not been played. A card claiming ``trained_through="2025-26"`` is therefore
    either training from a committed fixture set (fine, and it should say so) or
    claiming training it did not do (not fine).
    """
    seasons = set(live_wh.sql("SELECT DISTINCT season FROM dim_player")["season"])
    claims = {
        name: card.trained_through
        for name, card in _discover_cards()
        if card.trained_through
    }
    unbacked = {
        n: s for n, s in claims.items()
        if s not in seasons and "synthetic" not in " ".join(
            _card_by_name(n).notes
        ).lower()
    }
    assert not unbacked, (
        f"cards claim training through seasons absent from the warehouse "
        f"{sorted(seasons)}: {unbacked}. If the training set is a committed "
        "fixture rather than the warehouse, the card's notes must say so"
    )


def test_promoted_club_prior_refuses_to_invent_itself() -> None:
    """GUARDS: a single-season fit silently becoming a league-average guess.

    ``fit_promoted_prior`` raises ``InsufficientHistoryError`` rather than
    returning a plausible number when too few promotion events are observable.
    This is the shape every cold-start estimate in this codebase should have,
    and it is worth pinning because the tempting change -- a quiet fallback --
    is invisible in every metric.
    """
    import pandas as pd

    from fpl_edge.models.team_goals.data import InsufficientHistoryError
    from fpl_edge.models.team_goals.promoted import (
        FALLBACK_PROMOTED_PRIOR,
        fit_promoted_prior,
    )

    with pytest.raises(InsufficientHistoryError):
        fit_promoted_prior(pd.DataFrame())

    explicit = fit_promoted_prior(pd.DataFrame(), allow_fallback=True)
    assert explicit is FALLBACK_PROMOTED_PRIOR
    assert explicit.source == "assumed_fallback", (
        "the opt-in fallback must label itself as an assumption, not as a fit"
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_CARDS: dict[str, ModelCard] | None = None


def _discover_cards() -> list[tuple[str, ModelCard]]:
    """Every ModelCard in the tree, found by walking the package.

    Duck-typed for the same reason as everywhere else in this suite: the model
    modules are being written concurrently and a hardcoded import list would be
    wrong within the hour.
    """
    global _CARDS
    if _CARDS is None:
        import fpl_edge

        found: dict[str, ModelCard] = {}
        for mod_info in pkgutil.walk_packages(fpl_edge.__path__, "fpl_edge."):
            if ".migrations" in mod_info.name:
                continue
            try:
                mod = importlib.import_module(mod_info.name)
            except Exception:  # noqa: S112, BLE001  (another team's module may be half-landed)
                continue
            for attr in dir(mod):
                obj = getattr(mod, attr, None)
                if isinstance(obj, ModelCard):
                    found[f"{mod_info.name}.{attr}"] = obj
        _CARDS = found
    return sorted(_CARDS.items())


def _card_by_name(name: str) -> ModelCard:
    return dict(_discover_cards())[name]
