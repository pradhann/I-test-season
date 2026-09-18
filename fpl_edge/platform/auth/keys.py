"""The per-user Anthropic API key: encrypted at rest, decrypted per turn.

Google sign-in gives identity and nothing else. There is no OAuth grant that
lets a web application spend a visitor's Claude subscription, so model access
is bring your own key: each signed-in manager pastes a key from
``console.anthropic.com`` on the Account tab, the server stores it encrypted
under that account, and every turn that manager runs is metered on their own
Anthropic account.

AES-256-GCM from ``cryptography``, a fresh 12 byte nonce per encryption, and
the manager's user id as the associated data. A ciphertext moved to another
manager's row therefore fails to decrypt rather than silently authorising the
wrong account.

The row is keyed by user id rather than by Google subject, which is the same
identifier that names the per-user directory in ``fpl_edge/platform/users.py``.
That is what lets the operator's key be found whether the operator reached
the server through Google or from the machine it runs on, where an
unauthenticated request is the operator by deployment.

THE NEVER IN A LOG RULE. :class:`UserKey` carries the plaintext and defines
``__repr__`` and ``__str__`` that print the last four characters only, so an
f-string of one in a log line is safe by construction rather than by review.
The value reaches exactly two places: the response to the person who pasted
it, as ``last4``, and ``ClaudeAgentOptions.env`` for the turn that spends it.
It is never written to ``os.environ``, never put in argv, never a field on a
pydantic model, and never a dict value handed to ``logging``.
"""

from __future__ import annotations

import base64
import datetime as dt
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from fpl_edge.platform.auth import settings
from fpl_edge.platform.auth.sessions import connect

UTC = dt.UTC

#: Format only, which is a typo check and not a validity check. A key that is
#: the right shape and revoked still fails at the first turn, with Anthropic's
#: own words.
KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9_-]{20,}$")

#: Which secret encrypted a row. Version 1 is ``USER_KEY_ENC_SECRET``;
#: version 0 is a row written before the current secret and readable only
#: while ``USER_KEY_ENC_SECRET_PREV`` is set.
CURRENT_KEY_VERSION = 1

EMPTY_PASTE = "paste a key, the field was empty"
BAD_SHAPE = ("that does not look like an Anthropic API key. It starts with "
             "sk-ant-")
ROTATED_OUT = ("your stored key could not be read after a server key "
               "rotation. Paste it again on the Account tab.")

#: What a signed-in manager with no stored key is told, on the chat composer
#: and anywhere else a turn is refused. One string, so the composer and the
#: 403 body cannot word it differently.
NO_KEY_DETAIL = (
    "No Anthropic API key is stored for this account. Open the Account tab, "
    "paste a key from console.anthropic.com, and send the message again."
)
NO_KEY_BODY = {
    "error": "no_api_key",
    "detail": NO_KEY_DETAIL,
    "remediation_url": "/#account",
}


class KeyInvalid(ValueError):
    """A paste that is empty or not the shape of an Anthropic key."""


class KeyUnreadable(RuntimeError):
    """A stored row that the current secrets cannot decrypt."""


@dataclass(frozen=True)
class UserKey:
    """One decrypted key, alive for one turn.

    ``__repr__`` and ``__str__`` print ``last4`` only. A traceback that
    includes this object, a debugger that prints it and an f-string in a log
    line all show four characters.
    """

    value: str
    last4: str

    def __repr__(self) -> str:
        return f"UserKey(last4={self.last4!r})"

    def __str__(self) -> str:
        return self.__repr__()


@dataclass(frozen=True)
class KeyState:
    """What the Account tab shows about a stored key. No key material."""

    set: bool
    last4: str | None = None
    created_utc: str | None = None
    rotated_utc: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"set": self.set, "last4": self.last4,
                "created_utc": self.created_utc,
                "rotated_utc": self.rotated_utc}


NOT_SET = KeyState(set=False)


def _secret_material(name: str) -> bytes | None:
    raw = settings.setting(name)
    if not raw:
        return None
    try:
        material = base64.b64decode(raw, validate=True)
    except Exception:  # noqa: BLE001 - a raw 32 byte secret is also accepted
        material = raw.encode("utf-8")
    if len(material) != 32:
        raise RuntimeError(
            f"the key encryption secret must decode to 32 bytes, this one is "
            f"{len(material)}. Generate one with "
            f"python -c \"import base64,os;print(base64.b64encode(os.urandom(32)).decode())\""
        )
    return material


def _aesgcm(version: int):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    name = "key_secret" if version >= CURRENT_KEY_VERSION else "key_secret_prev"
    material = _secret_material(name)
    if material is None:
        raise KeyUnreadable(ROTATED_OUT)
    return AESGCM(material)


def key_storage_configured() -> bool:
    """True when a key can be stored, which needs the encryption secret."""
    try:
        return _secret_material("key_secret") is not None
    except RuntimeError:
        return False


def validate(pasted: str | None) -> str:
    """The stripped key, or :class:`KeyInvalid` with the owner's own words."""
    value = (pasted or "").strip() if isinstance(pasted, str) else ""
    if not value:
        raise KeyInvalid(EMPTY_PASTE)
    if not KEY_RE.match(value):
        raise KeyInvalid(BAD_SHAPE)
    return value


def _row(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM user_keys WHERE user_id = ?",
                        (user_id,)).fetchone()


def _store_exists(path: Path | str | None) -> bool:
    """Whether an auth database is already on disk.

    Reads answer "nothing stored" without creating one. A read that created
    the file would leave a SQLite database in the repo tree the first time
    anything asked whether the owner had a key, on a machine where sign-in is
    not configured at all. Writes still create it, which is the only time a
    store should come into existence.
    """
    return Path(path).exists() if path is not None else settings.auth_db_path().exists()


def state(user_id: str, *, path: Path | str | None = None) -> KeyState:
    """What is stored for one person, with no key material in it."""
    if not _store_exists(path):
        return NOT_SET
    with connect(path) as conn:
        row = _row(conn, user_id)
    if row is None:
        return NOT_SET
    return KeyState(set=True, last4=row["last4"],
                    created_utc=row["created_utc"],
                    rotated_utc=row["rotated_utc"])


def has_key(user_id: str, *, path: Path | str | None = None) -> bool:
    """Whether a row exists. Presence only, so nothing is decrypted to answer
    a tier check that is about to refuse the request anyway."""
    return state(user_id, path=path).set


def store(user_id: str, pasted: str, *, path: Path | str | None = None) -> KeyState:
    """Encrypt and write one key. A second write is a rotation in place.

    There is no history table, because a history table of API keys is a
    second place to leak from. A turn already in flight finishes on the key
    it started with, and no new turn sees the old one.
    """
    value = validate(pasted)
    nonce = os.urandom(12)
    ciphertext = _aesgcm(CURRENT_KEY_VERSION).encrypt(
        nonce, value.encode("utf-8"), user_id.encode("utf-8"))
    now = dt.datetime.now(UTC).isoformat()
    last4 = value[-4:]
    with connect(path) as conn:
        existing = _row(conn, user_id)
        if existing is None:
            conn.execute(
                "INSERT INTO user_keys (user_id, ciphertext, nonce, last4, "
                "key_version, created_utc) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, ciphertext, nonce, last4, CURRENT_KEY_VERSION, now),
            )
        else:
            conn.execute(
                "UPDATE user_keys SET ciphertext = ?, nonce = ?, last4 = ?, "
                "key_version = ?, rotated_utc = ?"
                " WHERE user_id = ?",
                (ciphertext, nonce, last4, CURRENT_KEY_VERSION, now, user_id),
            )
    return state(user_id, path=path)


def remove(user_id: str, *, path: Path | str | None = None) -> KeyState:
    """Delete the row. It does not sign the person out and does not touch
    their conversations. Revoking the key at Anthropic is a separate act, and
    the Account tab says so, because deleting this row does nothing about a
    key that has already leaked."""
    if not _store_exists(path):
        return NOT_SET
    with connect(path) as conn:
        conn.execute("DELETE FROM user_keys WHERE user_id = ?", (user_id,))
    return NOT_SET


def load(user_id: str, *, path: Path | str | None = None) -> UserKey | None:
    """Decrypt one key at the point of use, or None when none is stored.

    Called inside the turn that is about to spend tokens, never in the
    middleware that resolves the caller. A request that never calls a model
    never decrypts a key.
    """
    if not _store_exists(path):
        return None
    with connect(path) as conn:
        row = _row(conn, user_id)
        if row is None:
            return None
        version = int(row["key_version"])
        try:
            plain = _aesgcm(version).decrypt(
                bytes(row["nonce"]), bytes(row["ciphertext"]), user_id.encode("utf-8"))
        except KeyUnreadable:
            raise
        except Exception as exc:  # noqa: BLE001 - one honest class, no key in it
            detail = ROTATED_OUT if version < CURRENT_KEY_VERSION else (
                f"the stored key could not be decrypted "
                f"({type(exc).__name__}). Paste it again on the Account tab."
            )
            # `from None`: the cryptography exception carries no key material,
            # and a chained traceback through this frame is one more surface
            # for the ciphertext to appear in.
            raise KeyUnreadable(detail) from None
        value = plain.decode("utf-8")
    if version < CURRENT_KEY_VERSION:
        # Re-encrypt at the current secret on first use, which is what makes
        # a rotation window close on its own.
        store(user_id, value, path=path)
    return UserKey(value=value, last4=value[-4:])


def redact(text: str, key: UserKey | str | None) -> str:
    """Remove a key from text before it is stored or logged.

    The SDK streams the CLI's stderr into the conversation transcript. A CLI
    that echoed its own environment on a crash would otherwise put the key in
    a stored transcript, so the stream passes through here first.
    """
    value = key.value if isinstance(key, UserKey) else key
    if not value or not text:
        return text
    return text.replace(value, "sk-ant-[redacted]")
