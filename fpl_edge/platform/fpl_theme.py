"""The house chart style: Athletic/Opta grammar, enforced by import.

The python_viz sandbox pre-imports this module and calls :func:`apply` before
any user code runs, so every chart the agent produces wears the house style
by construction -- the agent writes content, the theme owns the look
(CHAT_ARCHITECTURE §4). The module is copied INTO the sandbox and imported
from there; it must therefore stay dependency-free beyond matplotlib.

What the style is, concretely:

- horizontal gridlines only, recessive; no top/right/left spines; the data
  sits on the page, not in a box.
- a title block set flush-left above the axes: bold title, muted subtitle.
  Never matplotlib's centred ``ax.set_title``.
- a footer line: source + as-of on the left, the wordmark on the right.
- one accent for one-measure charts; the reference palette for series;
  club colours ONLY for club-identity marks (they follow the entity, never a
  value); the app's diverging ramp for signed quantities, neutral at zero.
- direct labels over legends when there are few series; tabular numerals.

Rules the theme cannot enforce but the tool description repeats (dataviz
house law): one axis -- never a dual-axis chart; colour follows the entity,
never its rank; a diverging ramp needs a neutral midpoint; sort in SQL, not
in the figure.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

# ---------------------------------------------------------------- palette
#: Dark-first, matching the app's default theme. The sandbox may export
#: FPL_THEME_MODE=light before importing to flip.
import os as _os

import matplotlib.pyplot as plt
from matplotlib import font_manager as _fm  # noqa: F401 - registers system fonts

_MODE = _os.environ.get("FPL_THEME_MODE", "dark")

if _MODE == "light":
    BG, PANEL = "#ffffff", "#f4f5f7"
    INK, MUTED, FAINT, LINE = "#1a1d21", "#5b6067", "#8a8f96", "#d9dce1"
else:
    BG, PANEL = "#101214", "#1a1d21"
    INK, MUTED, FAINT, LINE = "#e8eaed", "#9aa0a6", "#6b7178", "#2a2e33"

#: One measure, one hue.
ACCENT = "#2a78d6"
#: The reference series palette (matches the app's s1..s4).
SERIES = ("#2a78d6", "#d67e2a", "#3d8b5f", "#8b5fd6")
#: Diverging, neutral at the midpoint — the fixtures ramp's vocabulary.
DIVERGING = ("#1d4f8f", "#2a78d6", "#7fb2e8", "#3a3e43",
             "#e8b27f", "#d67e2a", "#c74200")
GOOD, BAD, WARN = "#3d8b5f", "#c0392b", "#c77f00"

#: Club identity — mirrors web/dist/clubmark.css exactly. Colour follows the
#: ENTITY: paint a club's own mark/line with its colour, never a value.
CLUB = {
    1: "#da291c", 2: "#ffcd00", 3: "#ef0107", 4: "#241f20", 6: "#132257",
    7: "#670e36", 8: "#034694", 9: "#78d0f3", 11: "#003399", 14: "#c8102e",
    17: "#dd0000", 31: "#1b458f", 36: "#0057b8", 40: "#3a64a3", 43: "#6cabdd",
    54: "#1a1d21", 56: "#eb172b", 88: "#f18a01", 91: "#da291c", 94: "#e30613",
}


def club_color(team_code: int, fallback: str = ACCENT) -> str:
    return CLUB.get(int(team_code), fallback)


# ---------------------------------------------------------------- rcParams

_FONTS = ["Helvetica Neue", "Arial", "DejaVu Sans"]


def apply() -> None:
    """Set the house style globally. The sandbox calls this before user code."""
    plt.rcParams.update({
        "figure.facecolor": BG,
        "figure.dpi": 150,
        "figure.figsize": (8.4, 4.8),
        "savefig.facecolor": BG,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.28,
        "axes.facecolor": BG,
        "axes.edgecolor": LINE,
        "axes.labelcolor": MUTED,
        "axes.titlesize": 0,          # titles come from title_block, never here
        "axes.grid": True,
        "axes.grid.axis": "y",        # horizontal only; the Athletic grammar
        "axes.axisbelow": True,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.spines.left": False,
        "axes.spines.bottom": True,
        "grid.color": LINE,
        "grid.linewidth": 0.7,
        "grid.alpha": 0.55,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelsize": 9.5,
        "ytick.labelsize": 9.5,
        "xtick.major.size": 0,
        "ytick.major.size": 0,
        "font.family": "sans-serif",
        "font.sans-serif": _FONTS,
        "font.size": 10.5,
        "text.color": INK,
        "legend.frameon": False,
        "legend.fontsize": 9.5,
        "lines.linewidth": 2.0,
        "lines.solid_capstyle": "round",
        "patch.linewidth": 0,
    })


# ---------------------------------------------------------------- helpers

def title_block(fig, title: str, subtitle: str | None = None) -> None:
    """Flush-left editorial title block. THE way charts here are titled."""
    fig.text(0.012, 0.985, title, ha="left", va="top",
             fontsize=15, fontweight="bold", color=INK)
    if subtitle:
        fig.text(0.012, 0.922, subtitle, ha="left", va="top",
                 fontsize=10, color=MUTED)
    # leave room: callers should have created the figure BEFORE plotting via
    # plt.subplots(); we push the axes down for them.
    fig.subplots_adjust(top=0.82 if subtitle else 0.88)


def footer_source(fig, text: str) -> None:
    """Source + as-of, bottom-left; the wordmark bottom-right."""
    fig.text(0.012, 0.012, text, ha="left", va="bottom",
             fontsize=8, color=FAINT)
    fig.text(0.988, 0.012, "FPL EDGE", ha="right", va="bottom",
             fontsize=8, color=FAINT, fontweight="bold")
    fig.subplots_adjust(bottom=0.16)


def label_last_point(ax, xs, ys, text: str, color: str = INK) -> None:
    """Direct label at a line's end -- the legend-killer for few series."""
    ax.annotate(f"  {text}", (xs[-1], ys[-1]), va="center",
                fontsize=9.5, fontweight="bold", color=color,
                annotation_clip=False)


def tabular(ax) -> None:
    """Tabular numerals on both tick sets (alignment for scanned figures)."""
    for lab in list(ax.get_xticklabels()) + list(ax.get_yticklabels()):
        lab.set_fontfamily("monospace")


def value_grid(ax, axis: str = "x") -> None:
    """Point the grid at the VALUE axis. The default style grids y (right for
    column/line charts); a barh chart's values run along x, so call
    ``value_grid(ax)`` after ``barh`` or the gridlines separate bars instead
    of helping read them."""
    ax.grid(False)
    ax.grid(True, axis=axis, color=LINE, linewidth=0.7, alpha=0.55)
    ax.set_axisbelow(True)


def zero_line(ax, axis: str = "y") -> None:
    """A visible-but-recessive zero for signed quantities."""
    if axis == "y":
        ax.axhline(0, color=MUTED, linewidth=0.9, zorder=2)
    else:
        ax.axvline(0, color=MUTED, linewidth=0.9, zorder=2)
