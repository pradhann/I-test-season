"""``python -m fpl_edge.myteam`` -- the same sub-app the `fpl` CLI mounts.

Exists so the squad commands are runnable without the main CLI having to know
about this package, which keeps the wiring in ``fpl_edge/cli/main.py`` down to
one import and one ``add_typer``.
"""

from fpl_edge.myteam.cli import app

if __name__ == "__main__":
    app()
