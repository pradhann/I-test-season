"""How the Cookie header gets in.

An interactive hidden prompt could never accept an FPL cookie: a terminal in
canonical mode truncates a typed line at ~1024 bytes and the header is roughly
three times that, so the prompt stops taking keystrokes partway through and
reads as a hang. The working routes are a pipe and a file; these tests pin
them, and pin the guard that names the truncation instead of storing half a
credential.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from fpl_edge.myteam.cli import (
    _TTY_LINE_LIMIT,
    _CookieInputError,
    _read_cookie_input,
)

COOKIE = "access_token=aaa; refresh_token=bbb"


def test_a_file_is_read_verbatim(tmp_path: Path) -> None:
    f = tmp_path / "cookie.txt"
    f.write_text(COOKIE + "\n")
    assert _read_cookie_input(f) == COOKIE


def test_a_missing_file_says_which_file(tmp_path: Path) -> None:
    with pytest.raises(_CookieInputError, match="No such file"):
        _read_cookie_input(tmp_path / "absent.txt")


def test_an_empty_file_is_refused_rather_than_stored(tmp_path: Path) -> None:
    f = tmp_path / "cookie.txt"
    f.write_text("   \n")
    with pytest.raises(_CookieInputError, match="is empty"):
        _read_cookie_input(f)


def test_a_pipe_is_read_when_stdin_is_not_a_tty(monkeypatch) -> None:
    """`pbpaste | fpl myteam auth --paste-cookie` is the route that works.

    A pipe has no line-length limit, which is the whole point.
    """
    monkeypatch.setattr("sys.stdin", io.StringIO(COOKIE + "\n"))
    assert _read_cookie_input(None) == COOKIE


def test_an_empty_pipe_points_at_the_working_routes(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("   "))
    with pytest.raises(_CookieInputError) as exc:
        _read_cookie_input(None)
    assert "pbpaste" in str(exc.value) and "--from-file" in str(exc.value)


def test_a_prompt_at_the_line_limit_is_refused_as_truncated(monkeypatch) -> None:
    """Storing a truncated cookie is worse than failing.

    Half a header still parses far enough to look plausible, and the failure
    then surfaces hours later as an unexplained auth error.
    """
    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "x" * _TTY_LINE_LIMIT)
    with pytest.raises(_CookieInputError) as exc:
        _read_cookie_input(None)
    message = str(exc.value)
    assert "truncated" in message
    assert "pbpaste" in message, "a refusal must name a route that works"


def test_a_short_prompt_still_works(monkeypatch) -> None:
    """The prompt is a fallback, not a trap: a short paste is accepted."""
    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", _Tty())
    monkeypatch.setattr("typer.prompt", lambda *a, **k: COOKIE)
    assert _read_cookie_input(None) == COOKIE


def test_a_real_fpl_cookie_would_not_fit_in_a_typed_line() -> None:
    """The premise behind all of the above, asserted rather than assumed.

    Two RS256 JWTs plus the surrounding cookie jar; if this ever became false
    the prompt would be a fine primary route again.
    """
    jwt = "e" * 900          # an access/refresh JWT is comfortably this long
    realistic = f"req_language=en; access_token={jwt}; refresh_token={jwt}"
    assert len(realistic) > _TTY_LINE_LIMIT
