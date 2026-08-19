"""The on-disk registry: theses/open, theses/resolved, theses/scoreboard.

Plain files in git, on purpose. The registry's guarantees -- nothing is edited
after the fact, every resolution is attributable, the history is inspectable --
are exactly the guarantees a version-controlled directory of small text files
already has. A database would re-implement `git log` badly.

The store knows nothing about grading. It reads, writes and moves thesis files
and enforces the one invariant only the filesystem can: an id names exactly one
file, in exactly one of open/ or resolved/.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fpl_edge.theses.model import Thesis, ThesisStatus

DEFAULT_THESES_DIR = Path("theses")


class DuplicateThesisError(ValueError):
    """This id already exists. Theses are immutable; write a new one."""


@dataclass(frozen=True)
class ThesesStore:
    base: Path = DEFAULT_THESES_DIR

    @property
    def open_dir(self) -> Path:
        return self.base / "open"

    @property
    def resolved_dir(self) -> Path:
        return self.base / "resolved"

    @property
    def scoreboard_dir(self) -> Path:
        return self.base / "scoreboard"

    def ensure_layout(self) -> None:
        for d in (self.open_dir, self.resolved_dir, self.scoreboard_dir):
            d.mkdir(parents=True, exist_ok=True)

    # -- reads ---------------------------------------------------------------

    def path_of(self, thesis_id: str) -> Path | None:
        for d in (self.open_dir, self.resolved_dir):
            candidate = d / f"{thesis_id}.md"
            if candidate.exists():
                return candidate
        return None

    def _load_dir(self, directory: Path) -> list[tuple[Thesis, Path]]:
        out: list[tuple[Thesis, Path]] = []
        if not directory.exists():
            return out
        for path in sorted(directory.glob("*.md")):
            thesis = Thesis.from_markdown(path.read_text(encoding="utf-8"))
            if thesis.id != path.stem:
                raise ValueError(
                    f"{path}: front-matter id {thesis.id!r} does not match the "
                    f"filename. The filename is the identity; fix one of them."
                )
            out.append((thesis, path))
        return out

    def load_open(self) -> list[tuple[Thesis, Path]]:
        return self._load_dir(self.open_dir)

    def load_resolved(self) -> list[tuple[Thesis, Path]]:
        return self._load_dir(self.resolved_dir)

    def ids(self) -> set[str]:
        return {t.id for t, _ in self.load_open()} | {t.id for t, _ in self.load_resolved()}

    def idea_ids(self) -> set[str]:
        """Registry idea ids already mirrored to disk, for idempotent sync."""
        return {
            t.idea_id
            for t, _ in (*self.load_open(), *self.load_resolved())
            if t.idea_id
        }

    def unique_id(self, wanted: str) -> str:
        """`wanted`, or `wanted-2`, `wanted-3`... -- two theses in one day about
        the same player and claim are rare but legal."""
        existing = self.ids()
        if wanted not in existing:
            return wanted
        n = 2
        while f"{wanted}-{n}" in existing:
            n += 1
        return f"{wanted}-{n}"

    # -- writes --------------------------------------------------------------

    def write_open(self, thesis: Thesis) -> Path:
        if thesis.status is not ThesisStatus.OPEN:
            raise ValueError(f"write_open got a {thesis.status} thesis; use move_resolved")
        self.ensure_layout()
        if self.path_of(thesis.id) is not None:
            raise DuplicateThesisError(
                f"thesis id {thesis.id!r} already exists; ids are immutable. "
                f"Use ThesesStore.unique_id() before construction."
            )
        path = self.open_dir / f"{thesis.id}.md"
        path.write_text(thesis.to_markdown(), encoding="utf-8")
        return path

    def move_resolved(self, thesis: Thesis) -> tuple[Path, Path]:
        """Write the resolved thesis under resolved/ and remove the open file.

        Returns (old_path, new_path). Write-then-unlink, so a crash between the
        two leaves a duplicate to reconcile rather than a lost thesis.
        """
        if thesis.resolution is None:
            raise ValueError(f"{thesis.id} has no resolution block; grade it first")
        self.ensure_layout()
        old = self.open_dir / f"{thesis.id}.md"
        if not old.exists():
            raise FileNotFoundError(f"{thesis.id} is not in {self.open_dir}")
        new = self.resolved_dir / f"{thesis.id}.md"
        new.write_text(thesis.to_markdown(), encoding="utf-8")
        old.unlink()
        return old, new
