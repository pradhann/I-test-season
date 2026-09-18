"""Points: a match simulated end to end, then scored by the official rules.

Four modules, in the order the simulation uses them:

* :mod:`fpl_edge.models.points.shares` -- per-player scoring rates, shrunk
  toward a position prior by empirical Bayes, so 200 minutes of form does not
  outrank 3,000 minutes of record.
* :mod:`fpl_edge.models.points.model` -- the decomposed simulation. A scoreline
  is drawn from the team model, minutes from the minutes model, then goals and
  assists are allocated among the players who were on the pitch. Two defenders
  in one club keep the same clean sheet in the same draw, which is the whole
  reason points are not regressed on directly.
* :mod:`fpl_edge.models.points.bps` -- bonus, which is a rank statistic within
  a fixture and therefore has to be computed for all 22 players at once.
* :mod:`fpl_edge.models.points.scoring_map` -- the deterministic stat line to
  points map, kept apart from the simulator so it can be checked against every
  row of the archive.

Adding this file makes the directory a package, which has one measured
consequence recorded in ``docs/platform/ARCHITECTURE_REVIEW.md`` check 9.
``tests/audit/test_walk_forward.py:51`` builds its model-family set from
``pkgutil.iter_modules(fpl_edge.models.__path__)`` filtered on ``ispkg``, so
``points`` now joins that set, and the package has no ``evaluate.py``, so it
joins the ``missing`` list at :59 beside ``copying``, ``ensemble`` and
``field``. That test already fails on those three. The failure message names
four families instead of three; the failing test id does not change.

The two discovery walks at ``tests/audit/test_walk_forward.py:198`` and
``tests/audit/test_recency_chasing.py:236`` now import these four modules.
Nothing new is exposed to either: the one ``ModelCard`` in the package is built
in ``DecomposedPointsModel.__post_init__`` at ``model.py:53``, an instance
attribute rather than a module-level object, and there is no ``*Strategy`` name
here.

Nothing is re-exported on purpose. Callers import from the module that owns the
name.
"""
