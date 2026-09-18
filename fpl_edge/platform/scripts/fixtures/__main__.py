"""``python -m fpl_edge.platform.scripts.fixtures --build``.

The command `pipelines/registry.py` runs has to keep working now that the
module is a package, and a package answers ``-m`` from this file rather than
from a ``__main__`` guard in ``__init__``.

The guard below is load-bearing rather than decorative: the audit suite walks
every module in the tree with ``importlib.import_module``, and without it this
file would run the argparse parser and raise SystemExit during that walk.
"""

from __future__ import annotations

from fpl_edge.platform.scripts.fixtures.build import main

if __name__ == "__main__":
    raise SystemExit(main())
