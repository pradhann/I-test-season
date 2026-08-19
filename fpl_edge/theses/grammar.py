"""The closed grammar of falsifiable predictions, and one grader per template.

A ``falsifiable_prediction`` is not free text. It is a sentence from this
module's template set, chosen because every template here has a grader that can
settle it from the warehouse with no human judgement. The enforcement runs both
ways: :class:`~fpl_edge.theses.model.Thesis` refuses to construct a non-watch
thesis whose prediction does not parse, and this module refuses a template
without a grader by construction (they are defined together).

Design points that carry weight:

* **Comparator identity lives in the sentence or in the frozen codes, never in
  the resolver's present.** "outscores Haaland (code 223094)" embeds the code so
  grading needs no name resolution; "outscores positional price-peer median"
  grades against ``Thesis.comparator_codes`` frozen at creation. Either way the
  yardstick was fixed before kickoff.
* **A start is a start.** ``starts in N+ of GWa-GWb`` counts gameweeks in which
  the player started at least one match, from the warehouse's ``starts`` column
  where present and a 60-minute appearance where an archive season lacks it.
  The fallback is stated here rather than hidden because it is a real semantic
  difference (a 59-minute start exists) and the grader must not pretend
  otherwise.
* **Push is a real outcome.** "Outscores" is a strict inequality; a tie is a
  push, excluded from hit rates, exactly as the ideas registry treats it.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from fpl_edge.theses.model import ClaimType, Thesis, ThesisOutcome


class UngradeableClaimError(ValueError):
    """The string is not a sentence this grammar can grade.

    Raised at creation time, which is the whole point: an ungradeable claim must
    be refused (or demoted to ``watch`` with a note) before it reaches disk, not
    discovered at resolution when the window has already closed.
    """


@dataclass(frozen=True, slots=True)
class Grade:
    """What grading one thesis produced."""

    outcome: ThesisOutcome
    #: The realised quantity the claim was about (points, starts, involvements).
    observed: float
    #: What the claim required of it.
    target: float
    subject_points: float | None
    comparator_points: float | None
    detail: str

    @property
    def margin(self) -> float | None:
        """Subject minus comparator where a comparator exists, else observed - target."""
        if self.subject_points is not None and self.comparator_points is not None:
            return self.subject_points - self.comparator_points
        return self.observed - self.target


# -- realised-data helpers ---------------------------------------------------


def window_points(results: pd.DataFrame, codes: list[int], gws: range) -> pd.Series:
    """Total points per code over the window. Absent rows are 0, not NaN --
    an unused player returns nothing, which is FPL semantics, and NaN would let
    a blank quietly drop out of a median."""
    if results.empty or not codes:
        return pd.Series(dtype=float)
    rows = results[results["gw"].isin(list(gws)) & results["code"].isin(codes)]
    totals = rows.groupby("code")["total_points"].sum()
    return totals.reindex(codes).fillna(0.0).astype(float)


def _subject_points(results: pd.DataFrame, code: int, gws: range) -> float:
    pts = window_points(results, [int(code)], gws)
    return float(pts.iloc[0]) if not pts.empty else 0.0


def _comparator_median(results: pd.DataFrame, codes: tuple[int, ...], gws: range) -> float | None:
    """Median across members of each member's own window total.

    Not the sum of per-week medians: that figure belongs to a different player
    each week and describes a portfolio nobody could hold.
    """
    if not codes:
        return None
    totals = window_points(results, [int(c) for c in codes], gws)
    return float(totals.median()) if not totals.empty else None


def _starts_count(results: pd.DataFrame, code: int, gws: range) -> int:
    """Gameweeks in the window with at least one start for this player."""
    if results.empty:
        return 0
    mine = results[(results["code"] == int(code)) & results["gw"].isin(list(gws))]
    if mine.empty:
        return 0
    if "starts" in mine.columns and mine["starts"].notna().any():
        started = mine[mine["starts"].fillna(0).astype(int) >= 1]
    else:  # archive fallback: a 60-minute appearance
        started = mine[mine["minutes"].fillna(0).astype(int) >= 60]
    return int(started["gw"].nunique())


def _involvements(results: pd.DataFrame, code: int, gws: range) -> int:
    mine = results[(results["code"] == int(code)) & results["gw"].isin(list(gws))]
    if mine.empty:
        return 0
    goals = mine.get("goals_scored")
    assists = mine.get("assists")
    total = 0
    if goals is not None:
        total += int(goals.fillna(0).sum())
    if assists is not None:
        total += int(assists.fillna(0).sum())
    return total


def _beat(subject: float, comparator: float) -> ThesisOutcome:
    if subject > comparator:
        return ThesisOutcome.CORRECT
    if subject == comparator:
        return ThesisOutcome.PUSH
    return ThesisOutcome.INCORRECT


# -- templates ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Template:
    id: str
    pattern: re.Pattern[str]
    #: Renders params back into the one canonical sentence.
    render: Callable[..., str]
    #: (thesis, params, results) -> Grade
    grader: Callable[[Thesis, dict[str, str], pd.DataFrame], Grade]
    doc: str


def _window(params: dict[str, str]) -> range:
    return range(int(params["a"]), int(params["b"]) + 1)


def _grade_beats_peer_median(t: Thesis, p: dict[str, str], res: pd.DataFrame) -> Grade:
    gws = _window(p)
    subject = _subject_points(res, t.player_code, gws)
    comp = _comparator_median(res, t.comparator_codes, gws)
    if comp is None:
        return Grade(
            ThesisOutcome.VOID, subject, 0.0, subject, None,
            "comparator_codes is empty: no frozen peer set to grade against",
        )
    return Grade(
        _beat(subject, comp), subject, comp, subject, comp,
        f"{t.player} {subject:.0f} pts vs peer median {comp:.1f} "
        f"({len(t.comparator_codes)} frozen peers) over GW{p['a']}-GW{p['b']}",
    )


def _grade_beats_peer_median_by(t: Thesis, p: dict[str, str], res: pd.DataFrame) -> Grade:
    gws = _window(p)
    n = float(p["n"])
    subject = _subject_points(res, t.player_code, gws)
    comp = _comparator_median(res, t.comparator_codes, gws)
    if comp is None:
        return Grade(
            ThesisOutcome.VOID, subject, n, subject, None,
            "comparator_codes is empty: no frozen peer set to grade against",
        )
    margin = subject - comp
    outcome = ThesisOutcome.CORRECT if margin >= n else ThesisOutcome.INCORRECT
    return Grade(
        outcome, margin, n, subject, comp,
        f"{t.player} beat the frozen peer median by {margin:+.1f} pts "
        f"(needed +{n:.0f}) over GW{p['a']}-GW{p['b']}",
    )


def _grade_trails_peer_median(t: Thesis, p: dict[str, str], res: pd.DataFrame) -> Grade:
    gws = _window(p)
    subject = _subject_points(res, t.player_code, gws)
    comp = _comparator_median(res, t.comparator_codes, gws)
    if comp is None:
        return Grade(
            ThesisOutcome.VOID, subject, 0.0, subject, None,
            "comparator_codes is empty: no frozen peer set to grade against",
        )
    # An avoid call is correct when the subject scores FEWER.
    outcome = _beat(comp, subject)
    return Grade(
        outcome, subject, comp, subject, comp,
        f"{t.player} {subject:.0f} pts vs peer median {comp:.1f}; avoid call is "
        f"{'right' if outcome is ThesisOutcome.CORRECT else 'wrong'} "
        f"over GW{p['a']}-GW{p['b']}",
    )


def _grade_beats_named(t: Thesis, p: dict[str, str], res: pd.DataFrame) -> Grade:
    gws = _window(p)
    rival = int(p["code"])
    subject = _subject_points(res, t.player_code, gws)
    other = _subject_points(res, rival, gws)
    return Grade(
        _beat(subject, other), subject, other, subject, other,
        f"{t.player} {subject:.0f} pts vs {p['name']} {other:.0f} over GW{p['a']}-GW{p['b']}",
    )


def _grade_beats_top_captain(t: Thesis, p: dict[str, str], res: pd.DataFrame) -> Grade:
    gw = int(p["k"])
    gws = range(gw, gw + 1)
    rival = int(p["code"])
    subject = _subject_points(res, t.player_code, gws)
    other = _subject_points(res, rival, gws)
    return Grade(
        _beat(subject, other), subject, other, subject, other,
        f"GW{gw}: {t.player} {subject:.0f} pts vs most-captained {p['name']} {other:.0f}",
    )


def _grade_beats_captain_pool(t: Thesis, p: dict[str, str], res: pd.DataFrame) -> Grade:
    gws = _window(p)
    comp = _comparator_median(res, t.comparator_codes, gws)
    subject = _subject_points(res, t.player_code, gws)
    if comp is None:
        return Grade(
            ThesisOutcome.VOID, subject, 0.0, subject, None,
            "comparator_codes is empty: no frozen captain pool to grade against",
        )
    return Grade(
        _beat(subject, comp), subject, comp, subject, comp,
        f"{t.player} {subject:.0f} pts vs median of the {len(t.comparator_codes)} "
        f"most-captained {comp:.1f} over GW{p['a']}-GW{p['b']}",
    )


def _grade_starts(t: Thesis, p: dict[str, str], res: pd.DataFrame) -> Grade:
    gws = _window(p)
    n = int(p["n"])
    started = _starts_count(res, t.player_code, gws)
    outcome = ThesisOutcome.CORRECT if started >= n else ThesisOutcome.INCORRECT
    return Grade(
        outcome, float(started), float(n), None, None,
        f"{t.player} started {started} of GW{p['a']}-GW{p['b']} (needed {n}+)",
    )


def _grade_total_points(t: Thesis, p: dict[str, str], res: pd.DataFrame) -> Grade:
    gws = _window(p)
    n = float(p["n"])
    subject = _subject_points(res, t.player_code, gws)
    outcome = ThesisOutcome.CORRECT if subject >= n else ThesisOutcome.INCORRECT
    return Grade(
        outcome, subject, n, subject, None,
        f"{t.player} scored {subject:.0f} pts over GW{p['a']}-GW{p['b']} (needed {n:.0f}+)",
    )


def _grade_involvements(t: Thesis, p: dict[str, str], res: pd.DataFrame) -> Grade:
    gws = _window(p)
    n = int(p["n"])
    got = _involvements(res, t.player_code, gws)
    outcome = ThesisOutcome.CORRECT if got >= n else ThesisOutcome.INCORRECT
    return Grade(
        outcome, float(got), float(n), None, None,
        f"{t.player} had {got} goal involvements over GW{p['a']}-GW{p['b']} (needed {n}+)",
    )


_GW = r"GW(?P<a>\d{1,2})-GW(?P<b>\d{1,2})"
_NAME = r"(?P<name>[^()]+?)"

TEMPLATES: tuple[Template, ...] = (
    Template(
        "beats_peer_median",
        re.compile(rf"outscores positional price-peer median over {_GW}"),
        lambda a, b: f"outscores positional price-peer median over GW{a}-GW{b}",
        _grade_beats_peer_median,
        "Window total strictly beats the median window total of the frozen peer set.",
    ),
    Template(
        "beats_peer_median_by",
        re.compile(rf"outscores positional price-peer median by (?P<n>\d+)\+ pts over {_GW}"),
        lambda n, a, b: (
            f"outscores positional price-peer median by {n}+ pts over GW{a}-GW{b}"
        ),
        _grade_beats_peer_median_by,
        "Margin over the frozen peer median is at least N points.",
    ),
    Template(
        "trails_peer_median",
        re.compile(rf"scores fewer pts than positional price-peer median over {_GW}"),
        lambda a, b: f"scores fewer pts than positional price-peer median over GW{a}-GW{b}",
        _grade_trails_peer_median,
        "The avoid call: window total strictly below the frozen peer median.",
    ),
    Template(
        "beats_named_player",
        re.compile(rf"outscores {_NAME} \(code (?P<code>\d+)\) over {_GW}"),
        lambda name, code, a, b: f"outscores {name} (code {code}) over GW{a}-GW{b}",
        _grade_beats_named,
        "Window total strictly beats one named rival; the code makes it exact.",
    ),
    Template(
        "beats_top_captain",
        re.compile(
            rf"outscores the most-captained player {_NAME} \(code (?P<code>\d+)\) "
            r"in GW(?P<k>\d{1,2})"
        ),
        lambda name, code, k: (
            f"outscores the most-captained player {name} (code {code}) in GW{k}"
        ),
        _grade_beats_top_captain,
        "One-week captaincy call vs the most-captained player, frozen at creation.",
    ),
    Template(
        "beats_captain_pool_median",
        re.compile(rf"outscores the median of the frozen captain pool over {_GW}"),
        lambda a, b: f"outscores the median of the frozen captain pool over GW{a}-GW{b}",
        _grade_beats_captain_pool,
        "Window total strictly beats the median of the frozen most-captained pool.",
    ),
    Template(
        "starts_at_least",
        re.compile(rf"starts in (?P<n>\d+)\+ of {_GW}"),
        lambda n, a, b: f"starts in {n}+ of GW{a}-GW{b}",
        _grade_starts,
        "Started (warehouse `starts`; 60-minute fallback for archives) in N+ gameweeks.",
    ),
    Template(
        "total_points_at_least",
        re.compile(rf"scores (?P<n>\d+)\+ pts over {_GW}"),
        lambda n, a, b: f"scores {n}+ pts over GW{a}-GW{b}",
        _grade_total_points,
        "Window total is at least N points.",
    ),
    Template(
        "attacking_returns_at_least",
        re.compile(rf"returns (?P<n>\d+)\+ goal involvements over {_GW}"),
        lambda n, a, b: f"returns {n}+ goal involvements over GW{a}-GW{b}",
        _grade_involvements,
        "Goals plus assists over the window is at least N.",
    ),
)

_BY_ID = {t.id: t for t in TEMPLATES}


def parse(prediction: str) -> tuple[Template, dict[str, str]]:
    """Match a prediction against the grammar, or refuse it loudly."""
    text = " ".join(str(prediction).split())
    for template in TEMPLATES:
        m = template.pattern.fullmatch(text)
        if m:
            params = m.groupdict()
            if "a" in params and "b" in params and int(params["a"]) > int(params["b"]):
                raise UngradeableClaimError(
                    f"window runs backwards in {text!r}: GW{params['a']} > GW{params['b']}"
                )
            return template, params
    known = "\n  ".join(f"{t.id}: {t.pattern.pattern}" for t in TEMPLATES)
    raise UngradeableClaimError(
        f"{prediction!r} matches no claim template. A prediction must be a "
        f"sentence the grader can settle; known templates:\n  {known}\n"
        "If the idea cannot be said in one of these forms, store it as "
        "claim_type=watch with a note instead of a fake prediction."
    )


def grade(thesis: Thesis, results: pd.DataFrame) -> Grade:
    """Grade one thesis against finished per-fixture results.

    ``results`` must be point-in-time filtered by the caller (a Snapshot read);
    this function only compares, it never reads the warehouse.
    """
    if thesis.falsifiable_prediction is None:
        raise UngradeableClaimError(f"{thesis.id} is a watch thesis; nothing to grade")
    template, params = parse(thesis.falsifiable_prediction)
    return template.grader(thesis, params, results)


def render(template_id: str, **params: object) -> str:
    """Build a canonical prediction string, validated by round-trip."""
    template = _BY_ID.get(template_id)
    if template is None:
        raise UngradeableClaimError(
            f"unknown template {template_id!r}; known: {sorted(_BY_ID)}"
        )
    sentence = template.render(**params)
    parse(sentence)  # guarantee the renderer and the parser agree
    return sentence


def default_prediction(
    claim_type: ClaimType,
    *,
    gw_start: int,
    horizon_gws: int,
    captain_name: str | None = None,
    captain_code: int | None = None,
) -> str | None:
    """The canonical claim for a claim type, or None for watch.

    ``minutes`` defaults to starting two-thirds of the window (rounded up):
    strong enough to be wrong, weak enough to survive one rotation.
    """
    a, b = gw_start, gw_start + horizon_gws - 1
    if claim_type is ClaimType.WATCH:
        return None
    if claim_type in (ClaimType.BUY, ClaimType.OUT_OF_POSITION):
        return render("beats_peer_median", a=a, b=b)
    if claim_type is ClaimType.AVOID:
        return render("trails_peer_median", a=a, b=b)
    if claim_type is ClaimType.MINUTES:
        n = max(1, math.ceil(2 * horizon_gws / 3))
        return render("starts_at_least", n=n, a=a, b=b)
    if claim_type is ClaimType.CAPTAIN:
        if captain_name and captain_code:
            return render("beats_top_captain", name=captain_name, code=captain_code, k=a)
        return render("beats_captain_pool_median", a=a, b=b)
    raise UngradeableClaimError(f"no default template for claim_type {claim_type}")
