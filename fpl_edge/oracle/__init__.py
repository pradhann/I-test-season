"""The oracle: many weak signals, one traceable verdict per player.

Two modules:

* :mod:`fpl_edge.oracle.signals` -- the evidence layer. Every ``Signal``
  carries its source, its timestamp and its ``as_of``, so a signal published
  after a deadline is invisible to a decision taken before it, and a source's
  weight is its measured accuracy rather than an assumption.
* :mod:`fpl_edge.oracle.adapters` -- one thin adapter per data source. A source
  that is not wired contributes nothing and is reported as missing, never
  approximated from whatever else happens to be loaded.

This file exists so the directory is a package rather than a namespace package.
Without it ``pkgutil.walk_packages`` skips the directory, so the two audit
discovery walks in ``tests/audit/test_walk_forward.py:198`` and
``tests/audit/test_recency_chasing.py:236`` never see these modules. Neither
holds a module-level ``ModelCard`` or a ``*Strategy`` class, so making them
visible changes what is walked and not what is found.

Nothing is re-exported here on purpose. Callers import from the module that
owns the name, which keeps the import graph readable and keeps this file from
becoming a second place to look for a definition.
"""
