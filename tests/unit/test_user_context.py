"""The user context: who a request is, and where that manager's files live.

What is pinned here:

* a request with no session is the owner, with the entry id from
  ``FPL_ENTRY_ID`` when it is set and the committed default when it is not;
* a per-user path never escapes that user's own directory, whatever a user id
  or a file name contains;
* a user who is not the owner never sees the owner's entry id, never reaches a
  private FPL endpoint, and never gets a path into the owner's directory;
* the three panels that describe one manager's team no longer take ``entry_id``
  as a param, and the runner passes the context only to scripts that ask.
"""

from __future__ import annotations

import pytest

from fpl_edge.platform import users as U


@pytest.fixture(autouse=True)
def isolated_root(tmp_path, monkeypatch):
    """Every test in this file writes under its own data root."""
    monkeypatch.setenv(U.DATA_ROOT_ENV, str(tmp_path))
    monkeypatch.delenv("FPL_ENTRY_ID", raising=False)
    return tmp_path


# -- the owner default ------------------------------------------------------


def test_no_session_is_the_owner_with_the_committed_entry_id() -> None:
    from fpl_edge.config import USER

    ctx = U.current_user(None)
    assert ctx.user_id == U.OWNER_USER_ID
    assert ctx.entry_id == int(USER.entry_id)
    assert ctx.is_owner is True
    assert ctx.can_read_private is True


def test_fpl_entry_id_sets_the_owner_default(monkeypatch) -> None:
    """The deployment variable was documented and read by nothing."""
    monkeypatch.setenv("FPL_ENTRY_ID", "7654321")
    assert U.owner_context().entry_id == 7654321


def test_a_saved_team_id_beats_the_environment_default(monkeypatch) -> None:
    monkeypatch.setenv("FPL_ENTRY_ID", "7654321")
    U.write_profile(U.owner_context(), entry_id=222222, team_name="Other XI")
    assert U.owner_context().entry_id == 222222


def test_an_unusable_fpl_entry_id_is_loud(monkeypatch) -> None:
    from fpl_edge.config import owner_entry_id

    monkeypatch.setenv("FPL_ENTRY_ID", "not-a-number")
    with pytest.raises(RuntimeError, match="FPL_ENTRY_ID"):
        owner_entry_id()
    monkeypatch.setenv("FPL_ENTRY_ID", "0")
    with pytest.raises(RuntimeError, match="positive"):
        owner_entry_id()


def test_resolve_identity_returns_none_until_workstream_e_lands() -> None:
    """The seam E2 fills. Shipping it as None is what makes every request the
    owner and keeps the server behaving as it does today."""
    assert U.resolve_identity(object()) is None


# -- paths ------------------------------------------------------------------


def test_a_per_user_path_never_escapes_the_user_directory() -> None:
    ctx = U.context_for(U.Identity(user_id="abc123", entry_id=1))
    root = ctx.data_root.resolve()
    assert ctx.path("plans", U.TRANSFER_PLAN_NAME).is_relative_to(root)
    for escape in (("..",), ("..", "owner"), ("../owner/profile.json",),
                   ("/etc/passwd",), ("plans", "..", "..", "owner")):
        with pytest.raises(U.UserIdInvalid):
            ctx.path(*escape)


def test_a_user_id_that_is_a_path_is_refused() -> None:
    for bad in ("../../etc", "a/b", ".", "", "OWNER/..", "a" * 65):
        with pytest.raises(U.UserIdInvalid):
            U.context_for(U.Identity(user_id=bad))


def test_two_users_get_two_directories() -> None:
    a = U.context_for(U.Identity(user_id="aaa111", entry_id=11))
    b = U.context_for(U.Identity(user_id="bbb222", entry_id=22))
    assert a.data_root != b.data_root
    assert a.path("plans") != b.path("plans")


def test_the_owner_falls_back_to_the_pre_split_artefact(tmp_path) -> None:
    """Until the operator moves their files, the owner reads where they are.

    The fallback is the owner's alone: another user reads their own directory
    or nothing, which is what keeps one manager's plan out of another's panel.
    """
    legacy = tmp_path / "warehouse" / U.TRANSFER_PLAN_NAME
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}")

    owner = U.owner_context()
    assert owner.artefact(U.PLANS_DIR, U.TRANSFER_PLAN_NAME,
                          legacy=legacy) == legacy

    other = U.context_for(U.Identity(user_id="abc123", entry_id=1))
    assert other.artefact(U.PLANS_DIR, U.TRANSFER_PLAN_NAME,
                          legacy=legacy) != legacy

    mine = owner.ensure_dir(U.PLANS_DIR) / U.TRANSFER_PLAN_NAME
    mine.write_text("{}")
    assert owner.artefact(U.PLANS_DIR, U.TRANSFER_PLAN_NAME,
                          legacy=legacy) == mine


# -- what a user who is not the owner may reach -----------------------------


def test_a_non_owner_never_sees_the_owner_entry_id() -> None:
    from fpl_edge.config import USER

    ctx = U.context_for(U.Identity(user_id="abc123", entry_id=99))
    assert ctx.entry_id == 99
    assert ctx.entry_id != int(USER.entry_id)
    assert ctx.is_owner is False
    assert ctx.can_read_private is False


def test_a_non_owner_has_no_token_file_to_read() -> None:
    ctx = U.context_for(U.Identity(user_id="abc123", entry_id=99))
    with pytest.raises(U.PrivateReadDenied):
        ctx.token_env_path()


def test_a_non_owner_reaches_no_private_endpoint(monkeypatch) -> None:
    """The router builds the private client, so the check lives there.

    A user with no stored login of their own must not get as far as
    constructing one, whatever entry id they carry.
    """
    from fpl_edge.interfaces.qa import QuestionRouter

    built = []

    class Boom:
        def __init__(self, *a, **k):
            built.append(k)
            raise AssertionError("a private client was constructed")

    monkeypatch.setattr("fpl_edge.myteam.private.PrivateTeamClient", Boom)
    ctx = U.context_for(U.Identity(user_id="abc123", entry_id=99))
    router = QuestionRouter(None, season="2026-27", entry_id=99, user=ctx)
    assert router.user.can_read_private is False
    # The public and manual reads still run; neither touches my-team. The
    # warehouse handle is None here, so the public read is what raises.
    with pytest.raises(AttributeError):
        router._team_state()
    assert built == []


def test_an_entry_id_with_no_context_cannot_borrow_the_owner_login() -> None:
    """The id and the login come from one object. An id that is not the
    owner's therefore arrives with a context that has no login."""
    from fpl_edge.interfaces.qa import QuestionRouter

    router = QuestionRouter(None, season="2026-27", entry_id=12345)
    assert router.user.is_owner is False
    assert router.user.can_read_private is False

    owner = QuestionRouter(None, season="2026-27")
    assert owner.user.is_owner is True
    assert owner.entry_id == U.owner_context().entry_id


def test_a_token_manager_with_no_path_is_a_type_error() -> None:
    from fpl_edge.myteam.tokens import TokenManager

    with pytest.raises(TypeError):
        TokenManager()


# -- the panel contract -----------------------------------------------------


def test_no_registered_script_declares_entry_id_as_a_param() -> None:
    import fpl_edge.platform.scripts  # noqa: F401 - registration is the import
    from fpl_edge.platform import registry

    offenders = [
        name for name in registry.registered()
        if "entry_id" in (registry.script(name).params_schema.get("properties") or {})
    ]
    assert offenders == []


def test_the_runner_passes_the_context_only_to_scripts_that_ask() -> None:
    import fpl_edge.platform.scripts  # noqa: F401 - registration is the import
    from fpl_edge.platform import registry

    takes = {n for n in registry.registered()
             if registry.accepts_ctx(registry.script(n).fn)}
    assert {"squad_overview", "dashboard_brief", "planner_grid"} <= takes
    assert "projection_table" not in takes
    assert "fixture_board" not in takes


def test_no_panel_script_module_binds_the_user_singleton() -> None:
    """A panel reads a context or it reads shared state. There is no third
    option, and ``USER`` in a panel module is how the second one comes back."""
    import importlib
    import pkgutil

    import fpl_edge.platform.scripts as pkg

    bound = []
    for mod in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        module = importlib.import_module(mod.name)
        if getattr(module, "USER", None) is not None:
            bound.append(mod.name)
    assert bound == []


def test_the_owner_id_is_set_in_exactly_one_place() -> None:
    """The literal lives at ``UserConfig.entry_id`` and nowhere else in the
    shipped tree, so changing whose engine this is stays a one-line diff."""
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        ["grep", "-rn", "--include=*.py", "--include=*.js", "--include=*.css",
         "--include=*.html", "4490171", "fpl_edge", "scripts", "web"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    hits = [line for line in out.stdout.splitlines() if line.strip()]
    assert len(hits) == 1, hits
    assert hits[0].startswith("fpl_edge/config.py:")
