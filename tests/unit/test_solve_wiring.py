"""The rank objective is reachable, and both report halves share one source.

Before this wiring, the reachability audit found ``ObjectiveMode.RANK_MV`` and
the whole ``sim`` package unreachable from any production entrypoint, and the
weekly report's squad section rendered a full plan while its transfer section
claimed no forecast was configured -- the two halves read different sources of
truth. These tests pin the wiring, not the solver's maths (the solver has its
own suite).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

REPO = Path(__file__).resolve().parents[2]


def test_fpl_solve_is_a_registered_command_with_a_rank_mode() -> None:
    """A user must be able to RUN the objective the engine is for."""
    from fpl_edge.cli.main import app

    result = CliRunner().invoke(app, ["solve", "--help"])
    assert result.exit_code == 0
    assert "--mode" in result.output
    assert "rank" in result.output
    assert "--deficit" in result.output


def test_weekly_configures_the_forecast_the_solve_committed(tmp_path, monkeypatch) -> None:
    """One solve, one source of truth for both report halves."""
    import fpl_edge.cli.main as cli
    from fpl_edge.myteam import report as myteam_report

    myteam_report.reset_providers()
    frame = pd.DataFrame({
        "code": [1, 1, 2, 2],
        "gw": [2, 3, 2, 3],
        "xpts": [4.0, 3.5, 2.0, 2.2],
        "p_play": [0.9, 0.9, 0.8, 0.8],
    })
    fc = tmp_path / "forecast.parquet"
    frame.to_parquet(fc, index=False)

    real_exists = Path.exists

    def fake_exists(self):
        if self.name == "forecast.parquet":
            return True
        return real_exists(self)

    real_read = pd.read_parquet
    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(pd, "read_parquet", lambda p, *a, **k: real_read(fc))
    try:
        cli._configure_report_providers()
        provs = myteam_report.providers()
    finally:
        myteam_report.reset_providers()

    assert "points_forecast" in provs, (
        "a committed forecast exists and the report was not wired to it"
    )
    assert provs["points_forecast"].name == "table:forecast.parquet"
    from fpl_edge.opt import ObjectiveMode

    assert provs["mode"] is ObjectiveMode.EXPECTED_POINTS, (
        "the surrogate must be chosen in writing, not defaulted to a mode "
        "whose provider is absent"
    )


def test_weekly_without_an_artefact_configures_nothing(monkeypatch) -> None:
    """No artefact -> the section reports the gap; nothing is invented."""
    import fpl_edge.cli.main as cli
    from fpl_edge.myteam import report as myteam_report

    myteam_report.reset_providers()
    monkeypatch.setattr(
        Path, "exists",
        lambda self: False if self.name == "forecast.parquet" else Path.is_file(self),
    )
    try:
        cli._configure_report_providers()
        provs = myteam_report.providers()
    finally:
        myteam_report.reset_providers()
    assert "points_forecast" not in provs


def test_the_gap_message_names_the_command_that_fixes_it() -> None:
    """`fpl solve` must be the stated fix, and it must exist (docs/CLI drift
    is the bug class that shipped a documented-but-missing auth command)."""
    from fpl_edge.cli.main import app
    from fpl_edge.myteam import report as report_mod
    import inspect

    src = inspect.getsource(report_mod)
    assert "fpl solve" in src
    result = CliRunner().invoke(app, ["--help"])
    assert "solve" in result.output


def test_configure_accepts_and_forwards_the_validator() -> None:
    """recommend() must actually receive what configure() was given."""
    import inspect

    from fpl_edge.myteam import report as myteam_report

    myteam_report.reset_providers()
    sentinel = object()
    myteam_report.configure(validator=sentinel)
    try:
        assert myteam_report.providers()["validator"] is sentinel
    finally:
        myteam_report.reset_providers()
    src = inspect.getsource(myteam_report)
    assert 'validator=_PROVIDERS.get("validator")' in src


def test_artefact_paths_are_anchored_to_the_repo_not_the_cwd() -> None:
    """The 0.4 bug class: a relative artefact path silently empties a section
    when the command runs from another directory."""
    import inspect

    import fpl_edge.cli.main as cli
    import fpl_edge.cli.solve as solve

    for mod in (cli, solve):
        src = inspect.getsource(mod)
        assert 'Path("data/warehouse' not in src, (
            f"{mod.__name__} builds a cwd-relative artefact path"
        )
