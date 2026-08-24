/* Solver: fire `fpl solve` and render the plan it committed.
   The run panel talks to the solve runner (one solve at a time, status file +
   log tail); the result panel reads the persisted gw1_plan.json through
   /api/solve/plan, which resolves player codes to names server-side. The
   rank-vs-points DIFF from the latest `--mode both` log gets its own card —
   the two objectives disagreeing is the whole reason this engine exists. */

import { getJSON, postJSON, el, emptyBox, errBox, playerCard, fmtPrice, fmt1 } from "/js/app.js";

const POLL_MS = 3000;

function card(title, sub) {
  const c = el("section", "card");
  c.appendChild(el("h2", null, title));
  if (sub) c.appendChild(el("p", "sub", sub));
  const body = el("div");
  c.appendChild(body);
  c.body = body;               // refreshes clear the body, never the header
  return c;
}

function logPre() {
  const pre = el("pre");
  pre.style.maxHeight = "320px";
  pre.style.overflow = "auto";
  pre.style.background = "var(--raised)";
  pre.style.padding = "8px";
  pre.style.borderRadius = "6px";
  pre.style.fontSize = "11.5px";
  return pre;
}

// ---------- run panel ----------

function statusChip(state) {
  const cls = state === "running" ? "chip warn"
    : state === "done" ? "chip good"
    : state === "failed" ? "chip bad" : "chip";
  return el("span", cls, state || "idle");
}

function describeStatus(s) {
  const bits = [];
  if (s.mode) bits.push(`mode ${s.mode}`);
  if (s.started_utc) bits.push(`started ${s.started_utc.slice(0, 19)}Z`);
  if (s.finished_utc) bits.push(`finished ${s.finished_utc.slice(0, 19)}Z`);
  if (s.exit_code != null) bits.push(`exit ${s.exit_code}`);
  if (s.reason) bits.push(s.reason);
  return bits.join(" · ");
}

function runPanelInto(host, onFinished) {
  const controls = el("div", "filters");
  const mode = el("select");
  for (const m of ["both", "rank", "points"]) {
    const o = el("option", null, m === "both" ? "both (prints the diff)" : m);
    o.value = m;
    mode.appendChild(o);
  }
  const btn = el("button", "primary", "Run solve");
  const chipHolder = el("span");
  chipHolder.appendChild(statusChip("idle"));
  const line = el("span", "sub", "");
  controls.append(el("label", null, "objective"), mode, btn, chipHolder, line);
  host.append(controls);

  const pre = logPre();
  pre.style.display = "none";
  host.appendChild(pre);

  let lastState = null;
  let timer = null;

  function apply(s) {
    chipHolder.textContent = "";
    chipHolder.appendChild(statusChip(s.state));
    line.textContent = describeStatus(s);
    btn.disabled = s.state === "running";
    btn.textContent = s.state === "running" ? "Solving…" : "Run solve";
    if (s.log_tail && s.log_tail.length) {
      pre.style.display = "";
      const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
      pre.textContent = s.log_tail.join("\n");
      if (atBottom) pre.scrollTop = pre.scrollHeight;
    }
    if (lastState === "running" && (s.state === "done" || s.state === "failed")) {
      onFinished(s);
    }
    lastState = s.state;
  }

  async function poll() {
    if (!host.isConnected) { clearInterval(timer); return; } // view left
    try { apply(await getJSON("/api/solve/status")); }
    catch (e) { line.textContent = String(e.message || e); }
  }

  btn.onclick = async () => {
    btn.disabled = true;
    try {
      const s = await postJSON("/api/solve", { mode: mode.value });
      if (s.already_running) {
        line.textContent = "a solve is already running — attached to it";
      }
      lastState = "running";     // so completion triggers the plan refresh
      apply(s);
    } catch (e) {
      btn.disabled = false;
      host.appendChild(errBox(e));
    }
  };

  timer = setInterval(poll, POLL_MS);
  poll();
}

// ---------- plan panel ----------

function lookup(players, code) {
  const p = players[String(code)]
    || { name: String(code), pos: "?", price: null, team: null };
  return { ...p, code };
}

function pcard(entry, mark) {
  const sub = [entry.team, entry.price != null ? fmtPrice(entry.price) : null]
    .filter(Boolean).join(" · ");
  return playerCard(entry, { mark: mark || null, sub });
}

function renderPlan(res, host) {
  const plan = res.plan, d = plan.gw1, players = res.players || {};
  const gw = plan.horizon_gws && plan.horizon_gws[0];

  // Honesty first: a plan solved for a past gameweek must say so.
  if (res.next_gw != null && gw != null && gw < res.next_gw) {
    host.appendChild(el("div", "err",
      `This plan targets GW${gw}, but the next open deadline is GW${res.next_gw}. ` +
      "It is a record of a past decision, not advice for this week — re-solve."));
  }
  if (res.reason) host.appendChild(el("p", "sub", res.reason));

  const mark = c => c === d.captain ? "C" : c === d.vice_captain ? "V" : null;

  const pitch = el("div", "pitch");
  const byPos = { GKP: [], DEF: [], MID: [], FWD: [], "?": [] };
  for (const c of d.starting_xi || []) {
    const p = lookup(players, c);
    (byPos[p.pos] || byPos["?"]).push([p, mark(c)]);
  }
  for (const posRow of ["GKP", "DEF", "MID", "FWD", "?"]) {
    if (!byPos[posRow].length) continue;
    const row = el("div", "row");
    byPos[posRow].forEach(([p, m]) => row.appendChild(pcard(p, m)));
    pitch.appendChild(row);
  }
  host.appendChild(pitch);

  const bench = el("div", "bench");
  (d.bench || []).forEach(c => bench.appendChild(pcard(lookup(players, c), mark(c))));
  host.appendChild(bench);

  const generated = new Date(plan.generated_at);
  const ageH = (Date.now() - generated) / 3.6e6;
  const meta = el("p", "sub");
  meta.textContent =
    `objective ${plan.objective_mode} = ${fmt1(plan.objective)} over GW${gw}` +
    `–${plan.horizon_gws[plan.horizon_gws.length - 1]}` +
    ` · chip ${d.chip || "none"} · bank after ${fmtPrice((d.bank_after ?? 0) / 10)}` +
    ` · solved ${generated.toISOString().slice(0, 16)}Z (${ageH.toFixed(1)}h ago)` +
    ` · ${plan.n_sims} sims · ${plan.solver}`;
  host.appendChild(meta);
  if (ageH > 24) {
    host.appendChild(el("div", "chip warn",
      `stale: ${Math.round(ageH)}h old — prices, injuries and odds have moved`));
  }
  for (const note of plan.notes || []) {
    host.appendChild(el("p", "sub", `solver note: ${note}`));
  }
}

function renderDiff(lines, host) {
  if (!lines || !lines.length) {
    host.appendChild(emptyBox(
      "No rank-vs-points diff recorded.",
      "Run a solve with mode 'both' — the diff of the two objectives is its whole point."));
    return;
  }
  const pre = logPre();
  pre.textContent = lines.join("\n");
  host.appendChild(pre);
  const cap = lines.find(l => l.startsWith("captain:"));
  if (cap && cap.includes("CHANGED")) {
    host.appendChild(el("div", "chip warn", "the objectives disagree on the captain"));
  } else if (cap) {
    host.appendChild(el("div", "chip good", "both objectives agree on the captain"));
  }
}

async function planInto(planCard, diffCard) {
  planCard.body.textContent = "";
  diffCard.body.textContent = "";
  try {
    const res = await getJSON("/api/solve/plan");
    if (!res.exists) {
      planCard.body.appendChild(emptyBox(res.reason || "no plan yet",
        "Run a solve above; the committed plan renders here."));
      diffCard.style.display = "none";
      return;
    }
    renderPlan(res, planCard.body);
    diffCard.style.display = "";
    renderDiff(res.diff_lines, diffCard.body);
  } catch (e) { planCard.body.appendChild(errBox(e)); }
}

export default async function solver(host) {
  const run = card("Run a solve",
    "Starts `fpl solve` as its own process — the CLI owns the warehouse lock. " +
    "One solve at a time; minutes, not seconds.");
  const plan = card("Committed plan",
    "The persisted artefact (gw1_plan.json) the weekly report renders — one source of truth.");
  const diff = card("Rank vs points",
    "Where the two objectives disagree, from the latest `--mode both` solve log.");
  host.append(run, plan, diff);

  runPanelInto(run.body, () => planInto(plan, diff));
  await planInto(plan, diff);
}
