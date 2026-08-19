from __future__ import annotations

from pathlib import Path

import pytest

from fpl_edge.config import USER, load_env, secret


def test_entry_and_leagues_are_configured() -> None:
    assert USER.entry_id == 4490171
    assert USER.team_name == "i-test"
    ids = {m.league_id for m in USER.mini_leagues}
    assert {76109, 82939, 1264466} <= ids


def test_rank_utility_encodes_going_for_the_win() -> None:
    """The user's refined brief sets P(rank=1) as the objective with top-1k as
    the progress measure. Operationally we steer on P(top 1k) -- P(rank=1) is
    unestimable noise at feasible simulation counts -- and report P(rank=1)
    alongside. Low risk_lambda is deliberate: going for the win means accepting
    a fat left tail rather than quietly hedging it away."""
    cfg = USER.rank_utility
    cfg.validate()
    assert cfg.target_rank == 1_000
    assert cfg.stretch_rank == 1
    assert 0 < cfg.risk_lambda <= 0.15


def test_missing_required_secret_fails_loudly(monkeypatch, tmp_path) -> None:
    """A misconfigured deployment must fail at startup, not at the deadline."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="not set"):
        secret("TELEGRAM_BOT_TOKEN")


def test_optional_secret_returns_none(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NOPE", raising=False)
    assert secret("NOPE", required=False) is None


def test_env_parsing_keeps_colons_in_values(monkeypatch, tmp_path) -> None:
    """Telegram tokens contain ':' -- splitting on it would corrupt them."""
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=123456:AA-Bb_Cc\n# comment\n\n")
    monkeypatch.chdir(tmp_path)
    assert load_env(Path(".env"))["TELEGRAM_BOT_TOKEN"] == "123456:AA-Bb_Cc"


def test_no_secret_is_hardcoded_in_the_config_module() -> None:
    """Guards against a token ever being pasted into committed source."""
    src = Path("fpl_edge/config.py").read_text()
    assert "TELEGRAM_BOT_TOKEN=" not in src
    assert ":AA" not in src  # the shape of a Telegram bot token


def test_past_rank_prior_is_recorded_honestly() -> None:
    """The engine must not be tuned as if the best season were the base rate."""
    assert USER.best_past_rank == 9_524
    assert USER.median_past_rank > 1_000_000
    assert len(USER.past_ranks) == 8
