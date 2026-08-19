"""The weekly idea review: one page per gameweek, committed to GitHub.

Runs from the nightly settlement job. For the most recently finalised gameweek
it renders a self-contained HTML page — every idea and how it resolved, the
bias probes, the creator scoreboard, per-GW observations — and commits it to
the reports repository. Committing only when the content changed keeps the
history one commit per real update rather than one per cron tick.

The reports repo is separate from the engine repo on purpose: reports are the
publishable output, the engine holds keys-adjacent config and 900MB of raw
archive that has no business near a remote.
"""

from __future__ import annotations

import datetime as dt
import html
import subprocess
from pathlib import Path

from fpl_edge.config import USER
from fpl_edge.interfaces.bias import review as run_review
from fpl_edge.store import Warehouse

SEASON = "2026-27"
REPO_DIR = Path.home() / "Documents/Github/fpl-reports"
REPO_SLUG = "fpl-reports"

S1, S2, S3, S4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"


def esc(x) -> str:
    return html.escape(str(x))


def outcome_chip(outcome: str | None, status: str) -> str:
    if outcome == "correct":
        return '<span class="chip good">correct</span>'
    if outcome == "incorrect":
        return '<span class="chip bad">incorrect</span>'
    if outcome == "push":
        return '<span class="chip push">push</span>'
    return f'<span class="chip open">{esc(status)}</span>'


def margin_svg(rows: list[tuple[str, float]], *, w: int = 640) -> str:
    """Diverging bars: subject points minus comparator points per idea."""
    if not rows:
        return "<p class='muted'>No resolved ideas yet.</p>"
    hi = max(1.0, max(abs(v) for _, v in rows))
    bar_h, gap = 24, 8
    h = len(rows) * (bar_h + gap) + 24
    mid = 320
    out = [f'<svg viewBox="0 0 {w} {h}" role="img">']
    out.append(f'<line x1="{mid}" y1="4" x2="{mid}" y2="{h-20}" class="axis"/>')
    for i, (label, v) in enumerate(rows):
        y = i * (bar_h + gap) + 6
        bw = abs(v) / hi * (mid - 40)
        x = mid if v >= 0 else mid - bw
        cls = "pos" if v >= 0 else "neg"
        out.append(
            f'<rect x="{x:.1f}" y="{y}" width="{max(bw, 1):.1f}" height="{bar_h}" '
            f'rx="4" class="{cls}"><title>{esc(label)}: {v:+.1f} pts vs comparator'
            f'</title></rect>'
            f'<text x="{mid - 8 if v >= 0 else mid + 8}" y="{y + bar_h / 2 + 4}" '
            f'class="lab" text-anchor="{"end" if v >= 0 else "start"}">{esc(label)}</text>'
            f'<text x="{(x + bw + 6) if v >= 0 else (x - 6):.1f}" '
            f'y="{y + bar_h / 2 + 4}" class="val" '
            f'text-anchor="{"start" if v >= 0 else "end"}">{v:+.1f}</text>'
        )
    out.append(f'<text x="{mid}" y="{h - 6}" class="lab" text-anchor="middle">'
               f'← idea lost · idea won →</text></svg>')
    return "".join(out)


def build(wh: Warehouse, gw: int) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    rev = run_review(wh, season=SEASON)

    ideas = wh.sql(
        """
        SELECT i.*, v.stance, v.p_thesis_true, v.confidence AS v_conf
        FROM idea i LEFT JOIN idea_verdict v USING (idea_id)
        WHERE i.season = ? ORDER BY i.created_utc DESC
        """,
        [SEASON],
    )
    obs = wh.sql(
        "SELECT idea_id, gw, subject_points, comparator_points FROM idea_observation "
        "ORDER BY gw",
    )
    creators = wh.sql(
        """
        SELECT c.creator, count(*) AS claims,
               sum(CASE WHEN o.hit THEN 1 ELSE 0 END) AS hits,
               -- unscoreable is a VARCHAR reason; empty/NULL means scoreable
               sum(CASE WHEN o.hit IS NOT NULL
                        AND (o.unscoreable IS NULL OR o.unscoreable = '')
                        THEN 1 ELSE 0 END) AS resolved
        FROM content_claim c LEFT JOIN claim_outcome o USING (claim_id)
        GROUP BY c.creator ORDER BY resolved DESC, claims DESC LIMIT 15
        """,
    ) if _table_exists(wh, "content_claim") else None

    margins = []
    resolved_rows = []
    for r in ideas.itertuples():
        if r.subject_points is not None and r.comparator_points is not None:
            margins.append((str(r.subject_name or r.raw_text[:18]),
                            float(r.subject_points - r.comparator_points)))

    idea_rows = "".join(
        f"<tr><td>{esc(str(r.created_utc)[:16])}</td>"
        f"<td>{esc(r.raw_text[:60])}</td>"
        f"<td>{esc((r.thesis or '')[:90])}</td>"
        f"<td class='num'>{'' if r.p_thesis_true is None else f'{r.p_thesis_true:.0%}'}</td>"
        f"<td>{'yes' if r.acted else 'no'}</td>"
        f"<td>{outcome_chip(r.outcome, r.status)}</td></tr>"
        for r in ideas.itertuples()
    ) or "<tr><td colspan=6 class='muted'>No ideas yet — text the bot.</td></tr>"

    def _reading(f) -> str:
        if f.observed is None or f.expected is None:
            return "no data yet"
        return f"{f.observed:.0%} vs {f.expected:.0%} expected, n={f.n}"

    probe_rows = "".join(
        f"<tr><td>{esc(f.name)}</td><td class='num'>{esc(_reading(f))}</td>"
        f"<td class='muted'>{esc(f.question[:110])}</td></tr>"
        for f in rev.findings
    ) if rev.findings else ("<tr><td colspan=3 class='muted'>"
                            "Probes activate as ideas accumulate.</td></tr>")

    if creators is not None and not creators.empty:
        creator_rows = "".join(
            f"<tr><td>{esc(r.creator)}</td><td class='num'>{int(r.claims)}</td>"
            f"<td class='num'>{int(r.resolved or 0)}</td>"
            f"<td class='num'>{'' if not r.resolved else f'{(r.hits or 0) / r.resolved:.0%}'}</td></tr>"
            for r in creators.itertuples()
        )
    else:
        creator_rows = "<tr><td colspan=4 class='muted'>No claims ingested.</td></tr>"

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Idea Review — GW{gw}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{ --bg:#fbfcfa; --panel:#f2f4ef; --ink:#171a16; --ink2:#565b52;
  --line:#dde1d8; --good:#1a7f37; --bad:#c93c2c; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16181a; --panel:#1e2124;
  --ink:#f3f4f1; --ink2:#b3b8ad; --line:#2c3033; --good:#4bc26b; --bad:#f07862; }} }}
body {{ background:var(--bg); color:var(--ink); margin:0;
  font:15.5px/1.55 system-ui, sans-serif; }}
main {{ max-width:980px; margin:0 auto; padding:36px 22px 72px; }}
h1 {{ font-size:26px; margin:0 0 2px; }} h2 {{ font-size:18px; margin:36px 0 8px; }}
.muted {{ color:var(--ink2); }} .sub {{ color:var(--ink2); margin:0 0 20px; }}
table {{ border-collapse:collapse; width:100%; font-size:13.5px; }}
th,td {{ text-align:left; padding:6px 9px; border-bottom:1px solid var(--line);
  vertical-align:top; }}
th {{ color:var(--ink2); }} td.num {{ font-variant-numeric:tabular-nums; }}
.wrap {{ overflow-x:auto; background:var(--panel); border:1px solid var(--line);
  border-radius:8px; padding:8px; }}
.chip {{ font-size:11.5px; padding:2px 8px; border-radius:10px; white-space:nowrap; }}
.chip.good {{ background:var(--good); color:#fff; }}
.chip.bad {{ background:var(--bad); color:#fff; }}
.chip.open {{ background:var(--line); color:var(--ink2); }}
.chip.push {{ background:{S4}; color:#fff; }}
svg {{ width:100%; height:auto; }}
.pos {{ fill:{S3}; }} .neg {{ fill:{S2}; }}
.axis {{ stroke:var(--line); }} .lab {{ fill:var(--ink2); font-size:11px; }}
.val {{ fill:var(--ink); font-size:11px; font-variant-numeric:tabular-nums; }}
</style></head><body><main>
<h1>Idea Review — {esc(SEASON)} through GW{gw}</h1>
<p class="sub">Entry {USER.entry_id} ({esc(USER.team_name)}) · generated
{now:%Y-%m-%d %H:%M}Z · every idea ever texted, acted on or not</p>

<h2>Resolved margins — idea vs its comparator</h2>
<div class="wrap">{margin_svg(margins)}</div>

<h2>Every idea</h2>
<div class="wrap"><table>
<tr><th>when</th><th>you said</th><th>thesis</th><th>P(true) at submit</th>
<th>acted</th><th>outcome</th></tr>
{idea_rows}</table></div>

<h2>Bias probes</h2>
<p class="muted">Computed from context frozen at submission versus the
population base rate at the same instant — never asserted.</p>
<div class="wrap"><table>
<tr><th>probe</th><th>reading</th><th>detail</th></tr>{probe_rows}</table></div>

<h2>Creator scoreboard</h2>
<p class="muted">Hit rates appear as claims resolve; a creator with no resolved
claims carries zero weight in the oracle, whatever their following.</p>
<div class="wrap"><table>
<tr><th>creator</th><th>claims</th><th>resolved</th><th>hit rate</th></tr>
{creator_rows}</table></div>
</main></body></html>"""


def _table_exists(wh: Warehouse, table: str) -> bool:
    try:
        wh.sql(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def publish(page: str, gw: int) -> str:
    """Write, commit and push the page; create the private repo on first run."""
    if not REPO_DIR.exists():
        create = subprocess.run(
            ["gh", "repo", "create", REPO_SLUG, "--private",
             "--clone", "--description", "FPL edge engine: weekly idea reviews"],
            cwd=REPO_DIR.parent, capture_output=True, text=True,
        )
        if create.returncode != 0 and not REPO_DIR.exists():
            # The repo may already exist remotely; clone it instead.
            clone = subprocess.run(["gh", "repo", "clone", REPO_SLUG],
                                   cwd=REPO_DIR.parent, capture_output=True, text=True)
            if clone.returncode != 0:
                return (f"could not create or clone {REPO_SLUG}: "
                        f"{create.stderr.strip() or clone.stderr.strip()}")

    # gh's default remote is ssh; this machine authenticates gh over https
    # with the keyring token and has no ssh key loaded, so pin https.
    _git(["remote", "set-url", "origin",
          f"https://github.com/pradhann/{REPO_SLUG}.git"], REPO_DIR)

    out = REPO_DIR / f"{SEASON}-gw{gw:02d}.html"
    old = out.read_text() if out.exists() else ""
    # The timestamp line changes every run; compare without it so an unchanged
    # week produces no commit.
    import re

    strip = lambda t: re.sub(r"generated\n?.{0,30}Z", "", t)
    if strip(old) == strip(page):
        return f"unchanged: {out.name}"
    out.write_text(page)
    (REPO_DIR / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>FPL idea reviews</title><ul>"
        + "".join(f"<li><a href='{p.name}'>{p.stem}</a></li>"
                  for p in sorted(REPO_DIR.glob("*gw*.html"), reverse=True))
        + "</ul>"
    )
    _git(["add", "-A"], REPO_DIR)
    _git(["-c", "user.name=fpl-edge", "-c", f"user.email={USER.entry_id}@fpl-edge.local",
          "commit", "-m", f"Idea review through GW{gw}"], REPO_DIR)
    push = _git(["push", "-u", "origin", "HEAD"], REPO_DIR)
    if push.returncode != 0:
        return f"committed locally; push failed: {push.stderr.strip()[:200]}"
    return f"committed and pushed {out.name}"


def main() -> None:
    # A writer, not a read copy: the review path runs the idea-registry
    # migration on entry, which a read-only database refuses. The queries are
    # quick and the settlement job is sequential, so briefly holding the
    # writer is fine.
    with Warehouse() as wh:
        finished = wh.sql(
            "SELECT coalesce(max(gw), 0) AS g FROM fact_player_fixture WHERE season = ?",
            [SEASON],
        ).iloc[0]["g"]
        gw = max(int(finished), 0)
        page = build(wh, gw)
    print(publish(page, gw))


if __name__ == "__main__":
    main()
