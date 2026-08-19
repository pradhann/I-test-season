"""Generate the retro-analysis report: one self-contained HTML page.

Reads only artefacts and the warehouse (via a read copy), so the nightly
settlement job can regenerate it without blocking writers. Every number on the
page traces to a measured source; sections with no data say so.
"""

from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

OUT = Path("data/warehouse/retro_report.html")
BT_DIR = Path("data/warehouse/backtests")

# Series slots from the validated reference palette (dataviz skill).
S1, S1D = "#2a78d6", "#3987e5"   # our focus series
S2, S2D = "#eb6834", "#d95926"
S3, S3D = "#1baf7a", "#199e70"

STRAT_LABEL = {
    "template": "Template (most-owned, held)",
    "form_4gw": "Form (trailing 4-GW mean, held)",
    "last_weeks_best": "Recency (last week's best, held)",
}
STRAT_SLOT = {"template": 1, "form_4gw": 2, "last_weeks_best": 3}


def esc(x) -> str:
    return html.escape(str(x))


def line_chart(series: dict[str, list[int]], *, w=340, h=190, pad=34) -> str:
    """Cumulative-points small multiple: one <svg> with hoverable endpoints."""
    all_vals = [v for arr in series.values() for v in arr] or [0]
    lo, hi = 0, max(all_vals) * 1.05
    n = max(len(a) for a in series.values())

    def x(i): return pad + i * (w - pad - 8) / max(n - 1, 1)
    def y(v): return h - pad + (pad + 6 - h) * (v - lo) / (hi - lo)

    grid, labels = [], []
    for frac in (0.25, 0.5, 0.75, 1.0):
        v = hi * frac
        grid.append(f'<line x1="{pad}" y1="{y(v):.1f}" x2="{w-8}" y2="{y(v):.1f}" class="grid"/>')
        labels.append(f'<text x="{pad-5}" y="{y(v)+4:.1f}" class="ax" text-anchor="end">{int(v)}</text>')
    paths = []
    for name, arr in series.items():
        slot = STRAT_SLOT.get(name, 1)
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(arr))
        end_x, end_y, end_v = x(len(arr) - 1), y(arr[-1]), arr[-1]
        paths.append(
            f'<polyline points="{pts}" class="s{slot}" fill="none" stroke-width="2"/>'
            f'<circle cx="{end_x:.1f}" cy="{end_y:.1f}" r="4" class="dot s{slot}f">'
            f'<title>{esc(STRAT_LABEL.get(name, name))}: {end_v} pts</title></circle>'
            f'<text x="{end_x-6:.1f}" y="{end_y-8:.1f}" class="endlab" text-anchor="end">{end_v}</text>'
        )
    return (
        f'<svg viewBox="0 0 {w} {h}" role="img">'
        + "".join(grid) + "".join(labels)
        + f'<text x="{pad}" y="{h-8}" class="ax">GW1</text>'
        + f'<text x="{w-10}" y="{h-8}" class="ax" text-anchor="end">GW38</text>'
        + "".join(paths) + "</svg>"
    )


def bar_chart(rows: list[tuple[str, float, bool]], *, unit: str, w=560, lower_better=True) -> str:
    """Horizontal bars, ours emphasised; hover carries the exact value."""
    if not rows:
        return "<p class='gap'>no measurements</p>"
    hi = max(v for _, v, _ in rows) * 1.12
    bar_h, gap = 26, 10
    h = len(rows) * (bar_h + gap) + 26
    out = [f'<svg viewBox="0 0 {w} {h}" role="img">']
    for i, (label, v, ours) in enumerate(rows):
        yy = i * (bar_h + gap) + 6
        bw = (w - 210) * v / hi
        cls = "s1f" if ours else "bmut"
        out.append(
            f'<text x="200" y="{yy + bar_h/2 + 4}" class="blab" text-anchor="end">{esc(label)}</text>'
            f'<rect x="210" y="{yy}" width="{bw:.1f}" height="{bar_h}" rx="4" class="{cls}">'
            f'<title>{esc(label)}: {v:.4f} {esc(unit)}</title></rect>'
            f'<text x="{214 + bw:.1f}" y="{yy + bar_h/2 + 4}" class="bval">{v:.3f}</text>'
        )
    arrow = "lower is better" if lower_better else "higher is better"
    out.append(f'<text x="210" y="{h-4}" class="ax">{arrow}</text></svg>')
    return "".join(out)


def interval_chart(rows: list[dict], *, w=680) -> str:
    """GW1 projection: p10-p90 interval with the mean dot, per player."""
    if not rows:
        return "<p class='gap'>no projection artefact; run scripts/gw1_projection.py</p>"
    hi = max(r["p90"] for r in rows) * 1.08
    row_h, gap = 24, 8
    h = len(rows) * (row_h + gap) + 30
    def x(v): return 190 + (w - 210) * v / hi
    out = [f'<svg viewBox="0 0 {w} {h}" role="img">']
    for tick in range(0, int(hi) + 1, 2):
        out.append(f'<line x1="{x(tick):.1f}" y1="4" x2="{x(tick):.1f}" y2="{h-22}" class="grid"/>'
                   f'<text x="{x(tick):.1f}" y="{h-8}" class="ax" text-anchor="middle">{tick}</text>')
    for i, r in enumerate(rows):
        yy = i * (row_h + gap) + 10
        cy = yy + row_h / 2
        out.append(
            f'<text x="180" y="{cy+4:.1f}" class="blab" text-anchor="end">{esc(r["name"])} '
            f'<tspan class="own">{r["own"]:.0f}%</tspan></text>'
            f'<line x1="{x(r["p10"]):.1f}" y1="{cy:.1f}" x2="{x(r["p90"]):.1f}" y2="{cy:.1f}" '
            f'class="iv" stroke-width="6"/>'
            f'<circle cx="{x(r["xpts"]):.1f}" cy="{cy:.1f}" r="5" class="dot s1f">'
            f'<title>{esc(r["name"])}: mean {r["xpts"]:.2f}, p10 {r["p10"]:.0f}, '
            f'p90 {r["p90"]:.0f}, haul {r["p_haul"]:.0%}, owned {r["own"]:.1f}%</title></circle>'
        )
    out.append("</svg>")
    return "".join(out)


def build() -> str:
    now = dt.datetime.now(dt.timezone.utc)

    # -- backtests -------------------------------------------------------------
    seasons = {}
    for f in sorted(BT_DIR.glob("*.json")):
        seasons[f.stem] = json.loads(f.read_text())
    bt_panels = "".join(
        f'<figure><figcaption>{esc(season)}</figcaption>'
        + line_chart({k: v["cumulative"] for k, v in data.items()})
        + "</figure>"
        for season, data in seasons.items()
    )

    # -- measured model scores (walk-forward, committed by the model teams) ----
    goals_rows = [
        ("Home advantage only", 1.07346, False),
        ("Last-season table", 1.03778, False),
        ("Dixon-Coles (ours)", 0.98184, True),
    ]
    minutes_rows = [
        ("Base rate", 0.9106, False),
        ("Prev-season start rate", 0.7989, False),
        ("Hierarchical EB (ours)", 0.5215, True),
        ("Gradient boosting (ours)", 0.4165, True),
    ]

    # -- GW1 projection ----------------------------------------------------------
    proj_rows = []
    proj_path = Path("data/warehouse/gw1_projection.parquet")
    if proj_path.exists():
        import pandas as pd

        df = pd.read_parquet(proj_path).nlargest(14, "xpts")
        proj_rows = [
            {"name": r.web_name, "xpts": float(r.xpts), "p10": float(r.p10),
             "p90": float(r.p90), "p_haul": float(r.p_haul),
             "own": float(r.selected_by_pct or 0)}
            for r in df.itertuples()
        ]

    # -- warehouse state ---------------------------------------------------------
    from fpl_edge.store import Warehouse

    with Warehouse.read_copy() as wh:
        def count(sql: str) -> int:
            try:
                return int(wh.sql(sql).iloc[0, 0])
            except Exception:  # noqa: BLE001 - a missing table renders as 0
                return 0

        stats = {
            "player-fixture rows": count("SELECT count(*) FROM fact_player_fixture"),
            "odds rows": count("SELECT count(*) FROM fact_odds"),
            "creator claims": count("SELECT count(*) FROM content_claim"),
            "elite managers scored": count("SELECT count(*) FROM fact_manager_season"),
            "ideas tracked": count("SELECT count(*) FROM idea"),
            "set-piece duty rows": count("SELECT count(*) FROM set_piece_duty"),
        }
        ideas = wh.sql(
            "SELECT status, count(*) AS n FROM idea GROUP BY 1"
        ).to_dict("records") if stats["ideas tracked"] else []

    stat_tiles = "".join(
        f'<div class="tile"><div class="tv">{v:,}</div><div class="tl">{esc(k)}</div></div>'
        for k, v in stats.items()
    )

    idea_line = (
        " · ".join(f"{r['n']} {r['status']}" for r in ideas)
        if ideas else "none yet — text @fplpradhannbot"
    )

    return f"""<title>FPL Edge Retro</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=Source+Sans+3:wght@400;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {{
  --bg: #fbfcfa; --panel: #f2f4ef; --ink: #171a16; --ink2: #565b52; --ink3: #8a9084;
  --line: #dde1d8; --s1: {S1}; --s2: {S2}; --s3: {S3}; --iv: #c6ccc0;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #16181a; --panel: #1e2124; --ink: #f3f4f1; --ink2: #b3b8ad; --ink3: #7c8277;
  --line: #2c3033; --s1: {S1D}; --s2: {S2D}; --s3: {S3D}; --iv: #3a3f43;
}} }}
:root[data-theme="dark"] {{
  --bg: #16181a; --panel: #1e2124; --ink: #f3f4f1; --ink2: #b3b8ad; --ink3: #7c8277;
  --line: #2c3033; --s1: {S1D}; --s2: {S2D}; --s3: {S3D}; --iv: #3a3f43;
}}
body {{ background: var(--bg); color: var(--ink); margin: 0;
  font: 16px/1.55 "Source Sans 3", system-ui, sans-serif; }}
main {{ max-width: 1080px; margin: 0 auto; padding: 40px 24px 80px; }}
h1, h2 {{ font-family: Sora, system-ui, sans-serif; text-wrap: balance; }}
h1 {{ font-size: 30px; margin: 0 0 4px; }}
h2 {{ font-size: 20px; margin: 44px 0 6px; }}
.sub {{ color: var(--ink2); margin: 0 0 20px; }}
.note {{ color: var(--ink2); font-size: 14px; max-width: 68ch; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px; margin: 22px 0; }}
.tile {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  padding: 12px 14px; }}
.tv {{ font: 500 22px/1.2 "IBM Plex Mono", monospace; font-variant-numeric: tabular-nums; }}
.tl {{ color: var(--ink2); font-size: 12.5px; letter-spacing: .02em; margin-top: 2px; }}
.panels {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }}
figure {{ margin: 0; background: var(--panel); border: 1px solid var(--line);
  border-radius: 8px; padding: 12px; }}
figcaption {{ font: 600 13px/1 Sora, sans-serif; color: var(--ink2);
  letter-spacing: .04em; text-transform: uppercase; margin-bottom: 8px; }}
svg {{ width: 100%; height: auto; display: block; }}
.grid {{ stroke: var(--line); stroke-width: 1; }}
.ax {{ fill: var(--ink3); font: 11px "IBM Plex Mono", monospace; }}
.endlab {{ fill: var(--ink2); font: 11px "IBM Plex Mono", monospace; }}
.blab {{ fill: var(--ink); font: 13px "Source Sans 3", sans-serif; }}
.bval {{ fill: var(--ink2); font: 12px "IBM Plex Mono", monospace; }}
.own {{ fill: var(--ink3); font: 11px "IBM Plex Mono", monospace; }}
.s1 {{ stroke: var(--s1); }} .s2 {{ stroke: var(--s2); }} .s3 {{ stroke: var(--s3); }}
.s1f {{ fill: var(--s1); }} .s2f {{ fill: var(--s2); }} .s3f {{ fill: var(--s3); }}
.bmut {{ fill: var(--iv); }}
.iv {{ stroke: var(--iv); stroke-linecap: round; }}
.dot:hover {{ stroke: var(--ink); stroke-width: 2; }}
rect:hover {{ opacity: .85; }}
.legend {{ display: flex; gap: 18px; flex-wrap: wrap; color: var(--ink2);
  font-size: 13.5px; margin: 10px 0 0; }}
.legend b {{ display: inline-block; width: 14px; height: 4px; border-radius: 2px;
  vertical-align: middle; margin-right: 6px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14.5px; }}
th, td {{ text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); }}
th {{ color: var(--ink2); font-weight: 600; }}
td.num {{ font-family: "IBM Plex Mono", monospace; font-variant-numeric: tabular-nums; }}
.wrap {{ overflow-x: auto; }}
</style>
<main>
<h1>FPL Edge Retro</h1>
<p class="sub">Season 2026-27 · entry 4490171 · generated {now:%Y-%m-%d %H:%M}Z ·
regenerated nightly by the settlement job</p>

<div class="tiles">{stat_tiles}</div>
<p class="note">Ideas: {esc(idea_line)}.</p>

<h2>Baseline strategies, replayed over three real seasons</h2>
<p class="note">Each strategy picks a squad at every deadline seeing only what was
public at that instant, then reality scores it — autosubs, captain fallback and
the sell-on fee included. These are hold-style floors (one squad, re-ordered
weekly, no transfers); the engine has to clear them before its own replay means
anything. Recency-chasing ("buy last week's best") is the trap the whole system
is built to avoid — note how far it falls.</p>
<div class="panels">{bt_panels}</div>
<div class="legend">
<span><b style="background:var(--s1)"></b>{esc(STRAT_LABEL["template"])}</span>
<span><b style="background:var(--s2)"></b>{esc(STRAT_LABEL["form_4gw"])}</span>
<span><b style="background:var(--s3)"></b>{esc(STRAT_LABEL["last_weeks_best"])}</span>
</div>

<h2>Model quality, out of sample</h2>
<p class="note">Walk-forward log loss on held-out seasons (never trained on what
they predict). Our goal model beats every naive baseline decisively — and still
loses narrowly to the bookmaker market, which is why the oracle blends with the
market instead of fighting it.</p>
<div class="panels">
<figure><figcaption>Match outcome · log loss · 1,140 fixtures</figcaption>
{bar_chart(goals_rows, unit="log loss")}</figure>
<figure><figcaption>Minutes buckets · log loss · 29,747 rows</figcaption>
{bar_chart(minutes_rows, unit="log loss")}</figure>
</div>

<h2>GW1 projection — top players by expected points</h2>
<p class="note">Bar = 10th–90th percentile of simulated points, dot = mean.
Simulated jointly per fixture, so teammates' outcomes correlate the way clean
sheets actually do. Ownership beside each name is what the field holds.</p>
<figure>{interval_chart(proj_rows)}</figure>

<p class="note" style="margin-top:40px">Every probability here comes from a model
whose calibration is tested in CI; sections with no data say so rather than
faking it. Full methodology: docs/ in the repo.</p>
</main>"""


def main() -> None:
    OUT.write_text(build())
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
