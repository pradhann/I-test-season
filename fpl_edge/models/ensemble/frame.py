"""The one shape every projection -- ours or a stranger's -- is reduced to.

One row per ``(provider, season, gw, code)``:

======================  ====================================================
``xp``                  Expected FPL points, already multiplied through by
                        whatever probability of appearing the provider
                        believes. This is the number the ensemble averages.
``p_appear``            The provider's own P(any minutes), where published.
``xp_if_appears``       Expected points conditional on appearing.
======================  ====================================================

``xp`` is the only required column, because it is the only one every provider
publishes. The other two are kept where they exist because they fail
*differently*: a source can be right about who will haul and wrong about who
will play, and collapsing the two into a product hides which mistake it made.
Two of the four sources here publish the split, and on 2026-08-19 they disagreed
about how to encode an unavailable player -- FPL Form zeroes ``xp_if_appears``
for a flagged player while our own model zeroes ``p_appear``. That difference is
invisible in the product and would quietly corrupt any comparison of minutes
opinions.
"""

from __future__ import annotations

import pandas as pd

#: Required columns. Anything else is optional and passed through.
COLUMNS = ("provider", "season", "gw", "code", "xp")

OPTIONAL = ("p_appear", "xp_if_appears")

#: An FPL player cannot realistically be projected outside this band for one
#: gameweek. A projection outside it is a units bug (a season total in a weekly
#: column is the classic one), not an opinion.
XP_BOUNDS = (-5.0, 30.0)


class ProjectionFrameError(ValueError):
    """A projection frame is not the shape the ensemble can consume."""


def validate(frame: pd.DataFrame, *, name: str = "frame") -> pd.DataFrame:
    """Check and normalise a projection frame. Returns it with typed columns."""
    missing = [c for c in COLUMNS if c not in frame.columns]
    if missing:
        raise ProjectionFrameError(f"{name}: missing columns {missing}")
    if frame.empty:
        raise ProjectionFrameError(f"{name}: empty projection frame")

    out = frame.copy()
    out["gw"] = out["gw"].astype(int)
    out["code"] = out["code"].astype(int)
    out["xp"] = out["xp"].astype(float)

    if out["xp"].isna().any():
        raise ProjectionFrameError(
            f"{name}: {int(out['xp'].isna().sum())} null xp. A provider that has "
            f"no opinion about a player must be absent for that player, not "
            f"present with a null -- otherwise the ensemble cannot tell 'no "
            f"coverage' from 'projected zero'."
        )
    lo, hi = XP_BOUNDS
    bad = out[(out["xp"] < lo) | (out["xp"] > hi)]
    if not bad.empty:
        raise ProjectionFrameError(
            f"{name}: {len(bad)} xp value(s) outside [{lo}, {hi}], worst "
            f"{bad['xp'].abs().max():.2f}. That is not a gameweek projection."
        )
    dupes = out.duplicated(subset=["provider", "season", "gw", "code"])
    if dupes.any():
        raise ProjectionFrameError(
            f"{name}: {int(dupes.sum())} duplicate (provider, season, gw, code) rows"
        )
    for col in OPTIONAL:
        if col in out.columns:
            out[col] = out[col].astype(float)
    if "p_appear" in out.columns:
        p = out["p_appear"].dropna()
        if len(p) and (p.min() < -1e-9 or p.max() > 1 + 1e-9):
            raise ProjectionFrameError(
                f"{name}: p_appear outside [0, 1] (min {p.min():.3f}, max {p.max():.3f})"
            )
    return out.reset_index(drop=True)


def wide(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Align several providers onto one row per ``(season, gw, code)``.

    Missing cells stay NaN. They are not filled: an average that treats "this
    provider does not cover this player" as agreement with the others is exactly
    the bug that makes a consensus look more confident than it is.
    """
    if not frames:
        raise ProjectionFrameError("no frames to align")
    parts = [validate(f, name=str(f["provider"].iloc[0])) for f in frames]
    long = pd.concat(parts, ignore_index=True)
    out = long.pivot_table(
        index=["season", "gw", "code"], columns="provider", values="xp", aggfunc="first"
    )
    out.columns.name = None
    return out.reset_index()


def coverage(frame: pd.DataFrame, providers: list[str]) -> pd.DataFrame:
    """Per-provider count and share of the aligned rows that are populated."""
    total = len(frame)
    return pd.DataFrame([
        {"provider": p,
         "rows": int(frame[p].notna().sum()),
         "coverage": float(frame[p].notna().mean()) if total else 0.0}
        for p in providers if p in frame.columns
    ])
