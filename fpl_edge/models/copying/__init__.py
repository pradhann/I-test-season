"""Copying demonstrably skilled managers, and measuring whether it worked.

The thesis is that imitating managers with real records is a legitimate edge.
The risk is that "real record" is far harder to establish than it looks, and
that a shortlist built carelessly is a shortlist of lucky people. Every module
here is arranged around that risk.

:mod:`~fpl_edge.models.copying.skill`
    Empirical-Bayes separation of ability from luck across a manager's seasons,
    and a measurement of whether ability persists at all.
:mod:`~fpl_edge.models.copying.features`
    Strategy behaviour turned into columns: hits, transfers, chip timing,
    template overlap, differential counts, breakout capture.
:mod:`~fpl_edge.models.copying.effects`
    Cohort comparisons with effect sizes, intervals and FDR control, so an
    eighteen-feature sweep does not manufacture a finding.
:mod:`~fpl_edge.models.copying.template`
    What the skilled own that the field does not, ranked by copyable edge.
:mod:`~fpl_edge.models.copying.attribution`
    Whether copied picks and transfers actually paid, scored against
    counterfactuals rather than against zero.
:mod:`~fpl_edge.models.copying.minileague`
    The separate optimisation problem of beating 39 named people.
"""
