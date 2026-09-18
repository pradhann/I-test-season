"""The Account tab's sign-in and key blocks, rendered in node.

Three credentials meet on one card and they are three different things, so
the assertions here are mostly about what each block says rather than about
its markup. The card is the only place a manager is told that the Anthropic
key is billed to their own account and that removing it here does not revoke
it at Anthropic.

The harness is the one ``test_web_contract`` already uses: the view is
imported as an ES module under a DOM shim, and ``walk`` flattens the rendered
tree to ``{cls, text}``.
"""

from __future__ import annotations

import json

from fpl_edge.platform.auth import keys, oauth
from tests.unit.test_web_contract import VIEWS, _render, _strip_comments


def _first_text(nodes) -> str:
    return nodes[0]["text"]


# -- who you are -------------------------------------------------------------

def test_a_signed_out_visitor_is_offered_the_sign_in_link(tmp_path) -> None:
    me = {"signed_in": False, "sign_in_available": True, "anon_is_owner": False}
    nodes = _render(tmp_path, "account",
                    f"const host = document.createElement('div');\n"
                    f"view.renderIdentity(host, {json.dumps(me)}, () => {{}});\n"
                    f"const result = walk(host, []);")
    text = _first_text(nodes)
    assert "Not signed in" in text
    assert "Sign in with Google" in text
    # Google is told to give this server an address and an id, and the card
    # says exactly that rather than implying a broader grant.
    assert "your address and a stable id" in text
    src = _strip_comments(VIEWS["account"])
    assert '"/auth/google/start?next=" + encodeURIComponent("/#account")' in src


def test_a_signed_in_manager_sees_the_account_the_card_acts_on(tmp_path) -> None:
    me = {"signed_in": True, "sign_in_available": True, "anon_is_owner": False,
          "email": "someone@example.test", "is_operator": False,
          "key_set": False, "last4": None, "entry_id": None}
    nodes = _render(tmp_path, "account",
                    f"const host = document.createElement('div');\n"
                    f"view.renderIdentity(host, {json.dumps(me)}, () => {{}});\n"
                    f"const result = walk(host, []);")
    text = _first_text(nodes)
    assert "Signed in as someone@example.test" in text
    assert "Sign out" in text
    assert "operator" not in text


def test_the_owner_running_locally_is_not_asked_to_sign_in(tmp_path) -> None:
    """The server on the owner's Mac answers unauthenticated requests as the
    operator, so a sign-in button there would do nothing useful."""
    me = {"signed_in": False, "sign_in_available": False, "anon_is_owner": True}
    nodes = _render(tmp_path, "account",
                    f"const host = document.createElement('div');\n"
                    f"view.renderIdentity(host, {json.dumps(me)}, () => {{}});\n"
                    f"const result = walk(host, []);")
    text = _first_text(nodes)
    assert "Running as the owner" in text
    assert "Sign in with Google" not in text


def test_every_sign_in_failure_class_has_the_servers_own_sentence() -> None:
    """The classes are internal enums and none of them reaches the page. The
    sentences are the ones oauth.py defines, so the two cannot drift."""
    src = VIEWS["account"]
    for error_class, sentence in oauth.ERROR_MESSAGES.items():
        assert error_class in src, error_class
        # The first clause is enough to pin the wording without pinning the
        # line wrapping the JS file needs.
        assert sentence.split(",")[0].split(".")[0] in src.replace("\"\n    + \"", ""), (
            error_class)
    assert "${" not in src.split("const AUTH_ERROR")[1].split("};")[0]


# -- the key -----------------------------------------------------------------

def test_a_stored_key_shows_four_characters_and_where_to_revoke_it(
        tmp_path) -> None:
    info = {"set": True, "last4": "aB3x", "created_utc": "2026-09-18T00:00:00Z",
            "rotated_utc": None}
    nodes = _render(tmp_path, "account",
                    f"const host = document.createElement('div');\n"
                    f"view.renderKeyState(host, {json.dumps(info)});\n"
                    f"const result = walk(host, []);")
    text = _first_text(nodes)
    assert "aB3x" in text
    assert "console.anthropic.com" in text
    # Deleting the row here does nothing about a key that has already been
    # copied somewhere else, and the card says so.
    assert "does not revoke it at Anthropic" in text


def test_no_stored_key_says_what_stops_working(tmp_path) -> None:
    info = {"set": False, "last4": None, "created_utc": None,
            "rotated_utc": None}
    nodes = _render(tmp_path, "account",
                    f"const host = document.createElement('div');\n"
                    f"view.renderKeyState(host, {json.dumps(info)});\n"
                    f"const result = walk(host, []);")
    assert "No key stored" in _first_text(nodes)
    assert "403" in _first_text(nodes)


def test_the_key_input_is_a_password_field_and_is_cleared_after_every_send() -> None:
    """The paste is a credential and the page has no business holding it once
    the server has answered, which is the rule the FPL textarea already
    follows."""
    src = _strip_comments(VIEWS["account"])
    assert 'keyInput.type = "password"' in src
    assert 'keyInput.autocomplete = "off"' in src
    # Cleared before the request and again in the finally block.
    assert src.count("keyInput.value = \"\"") >= 2


def test_the_page_never_renders_a_key_it_was_given() -> None:
    """The routes return `set` and `last4` and never the key, so there is no
    branch that could print one. The assertion is on the absence of a read."""
    src = _strip_comments(VIEWS["account"])
    assert "info.key" not in src
    assert "out.key" not in src
    assert keys.NO_KEY_DETAIL not in src, (
        "the chat composer prints the server's remediation; the Account tab "
        "does not restate it"
    )


# -- csrf --------------------------------------------------------------------

def test_every_write_on_this_card_carries_the_csrf_header() -> None:
    src = _strip_comments(VIEWS["account"])
    assert '"X-CSRF-Token"' in src
    assert "itest_csrf" in src
    # postJSON from app.js does not send the header yet, so this view uses its
    # own sender for all five of its writes.
    assert "postJSON" not in src
    for path in ('"/auth/logout"', '"/api/account/key"',
                 '"/api/account/entry"', '"/api/account/connect"',
                 '"/api/account/verify"'):
        assert f"sendJSON({path}" in src, path
