"""The load-bearing test: my score and the field's score must be coupled.

Everything else in this package is machinery. This file is the claim. If these
assertions can be made to pass by a simulator that draws my score and the
field's score independently, the design is not doing what it says it is.

The tests run on the toy world so they are fast and offline, but the mechanism
under test is exactly the production one: a shared ``(n_players, n_sims)``
points draw consumed by both my squad and every sampled rival squad.
"""

from __future__ import annotations

import numpy as np
import pytest

from fpl_edge.sim.engine import SeasonSimulator, SquadPlan, greedy_squad
from fpl_edge.sim.field import FieldConfig
from fpl_edge.sim.rank import rank_from_scores
from fpl_edge.sim.synthetic import toy_world

GWS = (1, 2, 3, 4, 5, 6)


@pytest.fixture(scope="module")
def prepared():
    u, model, eo, cap, xp = toy_world(seed=5)

    class Own:
        card = None

        def forecast(self, snapshot, season, gw, expected_points=None):
            import pandas as pd

            return pd.DataFrame({"code": u.codes, "gw": int(gw),
                                 "eo_overall": eo, "captaincy_share": cap,
                                 "eo_top10k": eo})

    sim = SeasonSimulator(u, model, Own(), None, "toy", GWS, n_sims=3_000, seed=1,
                          field_config=FieldConfig(n_rivals=1_500, field_size=1_000_000))
    sim.prepare()
    return sim, u, eo, cap, xp


def _corr(a, b):
    return float(np.corrcoef(a, b)[0, 1])


def test_my_score_is_positively_correlated_with_the_field(prepared):
    """The headline number. Independent sampling would put this at zero."""
    sim, u, eo, _, xp = prepared
    squad = greedy_squad(u, eo, xp)
    d = sim.evaluate(SquadPlan(squad, label="template"))
    assert d.correlation_with_field() > 0.5
    assert d.beta_on_field() > 0.5


def test_correlation_rises_with_overlap_with_the_field(prepared):
    """The sign and the ordering, which is what actually prices a differential.

    Three squads on the same simulations: one built to own what the field owns,
    one built on expected points alone, and one built to avoid ownership. The
    coupling to the field must fall monotonically across them.
    """
    sim, u, eo, _, xp = prepared
    squads = {
        "template": greedy_squad(u, eo, xp),
        "xpts": greedy_squad(u, xp, xp),
        "contrarian": greedy_squad(u, xp - 12.0 * eo, xp),
    }
    corrs, overlaps = {}, {}
    start_share = sim._squads[GWS[0]].start_share(u.n_players)
    for name, sq in squads.items():
        d = sim.evaluate(SquadPlan(sq, label=name))
        corrs[name] = d.correlation_with_field()
        overlaps[name] = float(start_share[np.array(sq.starters)].sum())

    assert overlaps["template"] > overlaps["contrarian"]
    assert corrs["template"] > corrs["contrarian"], (
        f"correlation did not track overlap: {corrs} vs overlaps {overlaps}"
    )
    assert corrs["contrarian"] > 0.0, "even a contrarian squad shares the fixture list"


def test_pairwise_correlation_with_an_individual_rival_is_positive(prepared):
    """Not just with the field's mean, which could be positive for trivial reasons."""
    sim, u, eo, _, xp = prepared
    d = sim.evaluate(SquadPlan(greedy_squad(u, eo, xp)))
    riv = sim.rival_totals[:200].astype(np.float64)
    cors = np.array([_corr(d.my_scores, r) for r in riv])
    assert cors.mean() > 0.2
    assert (cors > 0).mean() > 0.95


def test_destroying_the_coupling_changes_the_answer(prepared):
    """The counterfactual an independent-sampling simulator would have produced.

    Rival season totals are reused verbatim but permuted across simulations.
    Every marginal distribution -- mine and the field's -- is untouched; only
    the joint is destroyed. Any change in P(top 10k) is attributable to
    correlation alone, and it must be material or the modelling effort is not
    earning its keep.
    """
    sim, u, eo, _, xp = prepared
    rng = np.random.default_rng(0)
    shuffled = sim.rival_totals[:, rng.permutation(sim.n_sims)]
    squad = greedy_squad(u, eo, xp)
    mine = sim.score_plan(SquadPlan(squad))
    n = sim.field_config.field_size
    r_true = rank_from_scores(mine, sim.rival_totals, field_size=n)
    r_ind = rank_from_scores(mine, shuffled, field_size=n)

    p_true = float((r_true <= 10_000).mean())
    p_ind = float((r_ind <= 10_000).mean())
    assert abs(p_true - p_ind) > 0.01, (
        f"correlation made no difference (correlated {p_true:.4f} vs "
        f"independent {p_ind:.4f}); the field model would be redundant"
    )
    assert np.std(r_true) < np.std(r_ind), (
        "sharing the field's outcomes must reduce the spread of my rank"
    )


def test_captaining_a_differential_lowers_the_beta_on_the_field(prepared):
    """Captaincy is where the coupling bites hardest, so test it directly."""
    sim, u, eo, _cap, xp = prepared
    squad = greedy_squad(u, eo, xp)
    field_cap = sim._squads[GWS[0]].captain_share(u.n_players)
    popular = max(squad.starters, key=lambda i: field_cap[i])
    rare = min(squad.starters, key=lambda i: field_cap[i])
    assert field_cap[popular] > field_cap[rare]

    all_gw = {g: squad.with_captain(popular) for g in GWS}
    a = sim.evaluate(SquadPlan(squad, overrides=all_gw, label="popular C"))
    b = sim.evaluate(SquadPlan(squad, overrides={g: squad.with_captain(rare) for g in GWS},
                               label="rare C"))
    assert a.beta_on_field() > b.beta_on_field()


def test_doubling_up_on_one_team_raises_variance(prepared):
    """A sanity check on the points model the field is scored against.

    If team-mates were independent, three players from one club would be no
    more volatile than three from three clubs, and every correlation number in
    this file would be measuring an artefact.
    """
    sim, u, _eo, _, _xp = prepared
    pts = sim._points[GWS[0]].astype(np.float64)
    teams = np.unique(u.team_code)
    same = np.flatnonzero(u.team_code == teams[0])[:3]
    diff = [int(np.flatnonzero(u.team_code == t)[0]) for t in teams[:3]]
    assert pts[same].sum(axis=0).std() > pts[np.array(diff)].sum(axis=0).std()
