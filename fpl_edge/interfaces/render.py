"""Images for the Telegram interface: the squad as a pitch, charts as PNGs.

Telegram renders no HTML and mangles wide tables, so anything visual ships as a
photo. Everything here draws with matplotlib's Agg backend (no display needed
under launchd) and returns PNG bytes; nothing touches the network or the
warehouse — callers pass plain data in, which keeps every renderer testable
offline and reusable by the weekly report.

Colors come from the validated dataviz reference palette so the phone, the
retro page and the weekly report read as one system.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

# Validated reference palette (dataviz skill), light-surface steps.
S1, S2, S3, S4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, INK2, LINE = "#171a16", "#565b52", "#dde1d8"
PITCH, PITCH_LINE = "#356a43", "#7ea98a"
BG = "#fbfcfa"

_POS_ROW = {1: 0, 2: 1, 3: 2, 4: 3}  # GKP high on the pitch, FWD at the bottom


@dataclass(frozen=True, slots=True)
class PitchPlayer:
    name: str
    position: int          # 1 GKP .. 4 FWD
    price_tenths: int
    is_captain: bool = False
    is_vice: bool = False
    on_bench: bool = False
    note: str = ""         # e.g. "inj", "4.2 xPts"


def _player_card(ax, x: float, y: float, p: PitchPlayer) -> None:
    label = p.name if len(p.name) <= 14 else p.name[:13] + "…"
    badge = " (C)" if p.is_captain else (" (V)" if p.is_vice else "")
    ax.add_patch(FancyBboxPatch(
        (x - 0.085, y - 0.028), 0.17, 0.075,
        boxstyle="round,pad=0.008", linewidth=0.8,
        facecolor="white", edgecolor=LINE, alpha=0.95, zorder=3,
    ))
    ax.text(x, y + 0.022, label + badge, ha="center", va="center",
            fontsize=8.3, color=INK, weight="bold" if p.is_captain else "normal",
            zorder=4)
    sub = f"£{p.price_tenths / 10:.1f}"
    if p.note:
        sub += f" · {p.note}"
    ax.text(x, y - 0.012, sub, ha="center", va="center",
            fontsize=7.2, color=INK2, zorder=4)


def squad_pitch_png(
    starters: list[PitchPlayer],
    bench: list[PitchPlayer],
    *,
    title: str,
    subtitle: str = "",
) -> bytes:
    """The manager's team drawn on a pitch, bench along the bottom."""
    fig, ax = plt.subplots(figsize=(7.2, 8.6), dpi=150)
    fig.patch.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # Pitch: stripes, box lines, halfway circle.
    ax.add_patch(plt.Rectangle((0.02, 0.14), 0.96, 0.80, facecolor=PITCH, zorder=0))
    for i in range(8):
        if i % 2 == 0:
            ax.add_patch(plt.Rectangle((0.02, 0.14 + i * 0.10), 0.96, 0.10,
                                       facecolor="white", alpha=0.045, zorder=1))
    for y0, y1, w in ((0.94, 0.86, 0.34), (0.94, 0.80, 0.56)):
        ax.add_patch(plt.Rectangle(((1 - w) / 2, y1), w, y0 - y1, fill=False,
                                   edgecolor=PITCH_LINE, linewidth=1.1, zorder=2))
    ax.add_patch(plt.Circle((0.5, 0.14), 0.11, fill=False,
                            edgecolor=PITCH_LINE, linewidth=1.1, zorder=2))

    # Rows by position, spread evenly.
    rows: dict[int, list[PitchPlayer]] = {0: [], 1: [], 2: [], 3: []}
    for p in starters:
        rows[_POS_ROW[p.position]].append(p)
    row_y = {0: 0.875, 1: 0.70, 2: 0.50, 3: 0.30}
    for r, players in rows.items():
        n = len(players)
        for i, p in enumerate(players):
            x = (i + 1) / (n + 1)
            _player_card(ax, x, row_y[r], p)

    ax.text(0.03, 0.105, "BENCH", fontsize=8, color=INK2, weight="bold")
    for i, p in enumerate(bench):
        _player_card(ax, 0.14 + i * 0.24, 0.055, p)

    ax.text(0.5, 0.985, title, ha="center", fontsize=13, weight="bold", color=INK)
    if subtitle:
        ax.text(0.5, 0.955, subtitle, ha="center", fontsize=9, color=INK2)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    return buf.getvalue()


def hbar_png(
    rows: list[tuple[str, float]],
    *,
    title: str,
    unit: str = "",
    highlight: set[str] | None = None,
) -> bytes:
    """Horizontal bars, largest on top, values labelled, ours highlighted."""
    highlight = highlight or set()
    labels = [r[0] for r in rows][::-1]
    values = [r[1] for r in rows][::-1]
    colors = [S1 if lab in highlight else "#b9c2b4" for lab in labels]

    fig, ax = plt.subplots(figsize=(7.0, 0.42 * len(rows) + 1.3), dpi=150)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    bars = ax.barh(labels, values, color=colors, height=0.62, zorder=3)
    for b, v in zip(bars, values):
        ax.text(b.get_width() + max(values) * 0.012, b.get_y() + b.get_height() / 2,
                f"{v:.2f}", va="center", fontsize=8.2, color=INK2)
    ax.set_title(title, fontsize=11.5, weight="bold", color=INK, loc="left")
    if unit:
        ax.set_xlabel(unit, fontsize=8.5, color=INK2)
    ax.tick_params(labelsize=8.6, colors=INK)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(LINE)
    ax.xaxis.grid(True, color=LINE, linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    plt.close(fig)
    return buf.getvalue()


def fixture_grid_png(
    teams: list[str],
    gws: list[int],
    difficulty: list[list[float]],
    opponents: list[list[str]],
    *,
    title: str,
) -> bytes:
    """Fixture-run grid: rows teams, columns gameweeks, colour = our own
    fitted difficulty (green easy, orange hard), opponent code in each cell."""
    from matplotlib.colors import LinearSegmentedColormap

    cmap = LinearSegmentedColormap.from_list("diff", [S3, "#e8e6da", S2])
    fig, ax = plt.subplots(figsize=(1.05 * len(gws) + 2.4, 0.42 * len(teams) + 1.2),
                           dpi=150)
    fig.patch.set_facecolor(BG)
    ax.imshow(difficulty, cmap=cmap, aspect="auto", vmin=0.0, vmax=1.0)
    ax.set_xticks(range(len(gws)), [f"GW{g}" for g in gws], fontsize=8.4, color=INK)
    ax.set_yticks(range(len(teams)), teams, fontsize=8.6, color=INK)
    for i in range(len(teams)):
        for j in range(len(gws)):
            ax.text(j, i, opponents[i][j], ha="center", va="center",
                    fontsize=7.6, color=INK)
    ax.set_title(title, fontsize=11.5, weight="bold", color=INK, loc="left")
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG)
    plt.close(fig)
    return buf.getvalue()
