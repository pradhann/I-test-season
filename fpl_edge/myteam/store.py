"""Where the manually-entered squad lives.

A JSON file per entry under ``data/myteam/``, not a warehouse table. The
warehouse is a point-in-time store of *observations about the game* -- prices,
fixtures, results -- with an append-only contract and a leakage audit over it.
A squad the manager typed into a chat window is neither an observation of the
game nor point-in-time-sensitive in that sense, and giving it an ``as_of`` would
imply a PIT guarantee that does not apply. Keeping it in a small, readable,
diffable file also means the manager can open it and see exactly what the engine
believes about their team, which matters more here than query performance over
one row.

The file is append-only in spirit: confirming a new squad adds a record, it does
not erase the previous one, so a reconciliation later can say what was believed
and when.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fpl_edge.myteam.manual import ManualEntryError, ManualSquadRecord, SquadDraft

UTC = dt.timezone.utc

DEFAULT_ROOT = Path("data/myteam")

#: Bump when the on-disk shape changes in a way an older reader cannot handle.
SCHEMA_VERSION = 1


class NoSuchDraftError(LookupError):
    """The confirm token does not match anything staged."""


@dataclass(frozen=True, slots=True)
class StoredState:
    version: int
    entry_id: int
    pending: dict | None
    confirmed: list[dict]

    def to_json(self) -> dict:
        return {
            "version": self.version,
            "entry_id": self.entry_id,
            "pending": self.pending,
            "confirmed": self.confirmed,
        }


class MyTeamStore:
    """Staged drafts and confirmed manual squads for one entry."""

    def __init__(self, entry_id: int, *, root: Path | str = DEFAULT_ROOT) -> None:
        self.entry_id = int(entry_id)
        self.root = Path(root)
        self.path = self.root / f"entry_{self.entry_id}.json"

    # -- io ------------------------------------------------------------------

    def _read(self) -> StoredState:
        if not self.path.exists():
            return StoredState(SCHEMA_VERSION, self.entry_id, None, [])
        body = json.loads(self.path.read_text())
        version = int(body.get("version", 0))
        if version > SCHEMA_VERSION:
            raise ManualEntryError(
                f"{self.path} was written by a newer version of this package "
                f"(schema {version} > {SCHEMA_VERSION}). Refusing to read it rather "
                "than silently dropping fields."
            )
        if int(body.get("entry_id", self.entry_id)) != self.entry_id:
            raise ManualEntryError(
                f"{self.path} holds entry {body.get('entry_id')}, not {self.entry_id}"
            )
        return StoredState(
            version=version,
            entry_id=self.entry_id,
            pending=body.get("pending"),
            confirmed=list(body.get("confirmed") or []),
        )

    def _write(self, state: StoredState) -> None:
        """Atomic replace, so an interrupted write cannot truncate the squad."""
        self.root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state.to_json(), indent=2, sort_keys=True) + "\n"
        fd, tmp = tempfile.mkstemp(dir=str(self.root), prefix=".myteam-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # -- staging -------------------------------------------------------------

    def stage(self, draft: SquadDraft) -> str:
        """Hold a validated draft pending confirmation. Returns the token."""
        if draft.record is None:
            raise ManualEntryError("cannot stage a draft that did not produce a squad")
        state = self._read()
        self._write(
            StoredState(
                version=SCHEMA_VERSION,
                entry_id=self.entry_id,
                pending=draft.record.to_json(),
                confirmed=state.confirmed,
            )
        )
        return draft.record.digest

    def pending(self) -> ManualSquadRecord | None:
        state = self._read()
        return ManualSquadRecord.from_json(state.pending) if state.pending else None

    def confirm(self, token: str, *, now: dt.datetime | None = None) -> ManualSquadRecord:
        """Promote the staged draft to the confirmed record.

        The token must match. Confirming by "yes" alone would make a stale draft
        from an hour ago -- or one the manager already decided against -- become
        the engine's model of the team on a one-word reply.
        """
        state = self._read()
        if not state.pending:
            raise NoSuchDraftError(
                "nothing is waiting to be confirmed. Send the 15 first."
            )
        record = ManualSquadRecord.from_json(state.pending)
        if token.strip().lower() != record.digest:
            raise NoSuchDraftError(
                f"token {token!r} does not match the squad waiting for confirmation "
                f"({record.digest}). Check the message that showed you the squad."
            )
        confirmed = ManualSquadRecord.from_json(
            {**record.to_json(), "confirmed_utc": (now or dt.datetime.now(UTC)).isoformat()}
        )
        self._write(
            StoredState(
                version=SCHEMA_VERSION,
                entry_id=self.entry_id,
                pending=None,
                confirmed=[*state.confirmed, confirmed.to_json()],
            )
        )
        return confirmed

    def discard(self) -> bool:
        state = self._read()
        if not state.pending:
            return False
        self._write(
            StoredState(SCHEMA_VERSION, self.entry_id, None, state.confirmed)
        )
        return True

    # -- reads ---------------------------------------------------------------

    def confirmed(self, *, season: str | None = None, gw: int | None = None
                  ) -> ManualSquadRecord | None:
        """The most recently confirmed squad, optionally filtered.

        Most recent wins, so re-entering a squad supersedes the previous one
        without deleting the history of what was believed before.
        """
        rows = self._read().confirmed
        for body in reversed(rows):
            if season is not None and body.get("season") != season:
                continue
            if gw is not None and int(body.get("gw", -1)) != int(gw):
                continue
            return ManualSquadRecord.from_json(body)
        return None

    def history(self) -> tuple[ManualSquadRecord, ...]:
        return tuple(ManualSquadRecord.from_json(b) for b in self._read().confirmed)
