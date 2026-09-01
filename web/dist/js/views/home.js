/* Dashboard — the front page rebuilt (FINAL_SPEC).

   PAGE ORDER, exact: topbar stats → provenance banner → alerts (uncapped,
   decision-typed) → THE PITCH → the solver card → briefing tiles (cap 6,
   suppression disclosed) → watch log → foot.

   THREE CALLS, in parallel: `squad_overview`, `dashboard_brief`,
   `GET /api/solve/plan`. Every number on this page comes from one of them;
   thresholds render from the brief's own `thresholds` echo — this file
   contains no gate constants. Wording lives HERE, keyed by rule/kind ids,
   because the brief carries no free-text recommendation field by contract.

   THE COLOUR LAW: the pitch's one encoded colour is the projection tier, on
   the --s1/--s2 series pair anchored at the XI median (bins documented in
   dashboard.css). --good/--warn/--bad stay reserved for the risk/status
   channel. The solver's objective stays in its own currency (rank_mv) and is
   never relabelled as xPts — the consensus delta for the swapped players is
   a separate, labelled quantity from a different voice.

   Every zone degrades alone (tryPanel memo + named gaps, the fixtures
   anatomy); the page never blanks. */

import { runPanel, getJSON, postJSON, el, errBox, provenance, stat,
         fmtPrice, fmt1, fmt2 } from "/js/app.js";
import { attachPlayerDrawer, showPlayerDetail } from "/js/components/playerdrawer.js";

const PHOTO = c =>
  `https://resources.premierleague.com/premierleague/photos/players/110x140/p${c}.png`;

/* ---------------------------------------------------------------- utils */

const num = v => (typeof v === "number" && isFinite(v) ? v : null);

function parseTs(s) {
  if (!s) return null;
  const d = new Date(String(s).replace(" ", "T").replace(/\+00:00$/, "Z"));
  return isNaN(d) ? null : d;
}
function ageText(iso) {
  const d = parseTs(iso);
  if (!d) return "age unknown";
  const h = (Date.now() - d.getTime()) / 3.6e6;
  if (h < 0) return "in the future?";
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}m`;
  if (h < 48) return `${Math.round(h)}h`;
  return `${Math.round(h / 24)}d`;
}
function clockText(iso) {
  const d = parseTs(iso);
  if (!d) return "unknown";
  return d.toISOString().slice(11, 16) + "Z";
}
function fmtSigned(v, digits = 0) {
  if (v == null) return "–";
  const s = Math.abs(v).toLocaleString(undefined,
    { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return (v >= 0 ? "+" : "−") + s;
}

/* A section with no data says WHICH data and WHY — never whitespace.
   Same anatomy as the fixtures view (fx-gap = defect, fx-note = decision). */
function namedGap(title, body) {
  const d = el("div", "fx-gap");
  d.appendChild(el("b", null, title));
  if (body instanceof Node) d.appendChild(body);
  else d.appendChild(document.createTextNode(body));
  return d;
}
function kvNote(title, body) {
  const d = namedGap(title, body);
  d.className = "fx-note";
  return d;
}

/* Panel call that reports failure as data (fixtures idiom, memoised 404s). */
const MISSING = new Map();
async function tryPanel(script, params = {}) {
  const gone = MISSING.get(script);
  if (gone) return { ok: false, error: gone, script, missing: true, cached: true };
  try {
    const { result, provenance: prov } = await runPanel(script, params);
    return { ok: true, result, prov, script };
  } catch (e) {
    const missing = /HTTP 404|no panel script named/.test(String(e.message || e));
    if (missing) MISSING.set(script, e);
    return { ok: false, error: e, script, missing };
  }
}

function card(title, sub) {
  const c = el("section", "card");
  if (title) c.appendChild(el("h2", null, title));
  if (sub) c.appendChild(el("p", "sub", sub));
  return c;
}

function citeChip(panel, asOf) {
  const b = el("button", "cite");
  b.type = "button";
  b.textContent = asOf ? `${panel} · ${ageText(asOf)}` : panel;
  b.title = asOf ? `as of ${asOf}` : `${panel}: no as-of instant served`;
  return b;
}

/* ------------------------------------------------------------------ view */

export default async function home(host) {
  const dh = attachPlayerDrawer("home");

  const statsRow = el("div", "stats");
  const provBanner = el("div", "db-prov");
  const alertsCard = card("Flags", null);
  const alertsBody = el("div", "db-alerts");
  alertsCard.appendChild(alertsBody);
  const pitchCard = card("My squad", null);
  const pitchBody = el("div");
  pitchCard.appendChild(pitchBody);
  const solverCard = card(null, null);
  solverCard.classList.add("solver");
  const tilesCard = card("The briefing", null);
  const tilesBody = el("div");
  tilesCard.appendChild(tilesBody);
  const watchCard = card("The watch stood", "every check, its result, its clock");
  const watchBody = el("div");
  watchCard.appendChild(watchBody);
  const foot = el("div", "db-foot");
  host.append(statsRow, provBanner, alertsCard, pitchCard, solverCard,
              tilesCard, watchCard, foot);

  const [sqR, brR, planR] = await Promise.all([
    tryPanel("squad_overview", {}),
    tryPanel("dashboard_brief", {}),
    getJSON("/api/solve/plan")
      .then(p => ({ ok: true, payload: p }))
      .catch(e => ({ ok: false, error: e })),
  ]);
  const sq = sqR.ok && !sqR.result.empty ? sqR.result : null;
  const brief = brR.ok && !brR.result.empty ? brR.result : null;
  const planPayload = planR.ok ? planR.payload : null;
  const thr = brief?.thresholds || {};
  const median = brief?.xi_median_xpts ?? null;

  const pitchCardByCode = new Map();   // code -> .pp element (for drills)
  const playerIndex = new Map();       // code -> best-known player object
  const remember = p => { if (p && p.code != null) playerIndex.set(p.code, p); };
  if (sq) [...(sq.starters || []), ...(sq.bench || [])].forEach(remember);
  for (const a of brief?.alerts || []) (a.players || []).forEach(remember);
  for (const t of brief?.tiles || []) remember(t.player);

  function openDrawer(code) {
    const p = playerIndex.get(code) || { code, name: String(code) };
    showPlayerDetail(dh, p, {});
  }

  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  function pulsePitch(codes) {
    pitchCard.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth",
                               block: "center" });
    for (const c of codes || []) {
      const elCard = pitchCardByCode.get(c);
      if (!elCard) continue;
      elCard.classList.add("flag-swap");
      if (!reduceMotion) elCard.classList.add("flag-pulse");
      setTimeout(() => elCard.classList.remove("flag-swap", "flag-pulse"), 1600);
    }
  }

  function drillTo(drill) {
    if (!drill) return;
    if (drill.drawer != null) return openDrawer(drill.drawer);
    if (drill.focus === "pitch") return pulsePitch(drill.codes);
    if (drill.focus === "solver")
      return solverCard.scrollIntoView({
        behavior: reduceMotion ? "auto" : "smooth", block: "center" });
    if (drill.tab) { location.hash = "#" + drill.tab; }
  }

  /* ---------------------------------------------------------- topbar row */
  {
    const gwLabel = brief?.gw ?? sq?.gw ?? null;
    if (gwLabel != null) statsRow.appendChild(stat(`GW${gwLabel}`, "gameweek"));
    if (sq) {
      if (sq.bank_tenths != null)
        statsRow.appendChild(stat(fmtPrice(sq.bank_tenths / 10), "bank"));
      if (sq.squad_value_tenths != null)
        statsRow.appendChild(stat(fmtPrice(sq.squad_value_tenths / 10), "squad value"));
      if (sq.projected_xi_xpts != null)
        statsRow.appendChild(stat(fmt1(sq.projected_xi_xpts),
                                  "XI xPts (consensus)"));
    }
    if (median != null)
      statsRow.appendChild(stat(fmt1(median), "XI median xPts"));
  }

  /* ------------------------------------------------- provenance banner */
  {
    provBanner.textContent = "";
    if (!sq) {
      provBanner.classList.add("warn");
      provBanner.textContent = "squad unreadable — the pitch below explains.";
    } else {
      const asOf = sq.as_of ? clockText(sq.as_of) : "unknown";
      const nextGw = brief?.gw ?? null;
      if (nextGw != null && sq.gw != null && sq.gw < nextGw) {
        provBanner.classList.add("warn");
        provBanner.appendChild(el("span", "chip warn",
          `pitch shows GW${sq.gw} picks`));
        provBanner.appendChild(document.createTextNode(
          ` — pending transfers are not visible here. `
          + `squad: ${sq.provenance_source} · as of ${asOf}`));
      } else {
        provBanner.textContent =
          `squad: ${sq.provenance_source} · as of ${asOf}`;
      }
    }
  }

  /* ------------------------------------------------------------- alerts */
  renderAlerts();
  function alertAvatar(code) {
    const img = el("img", "avatar");
    img.loading = "lazy"; img.alt = "";
    img.src = PHOTO(code);
    img.onerror = () => { img.onerror = null; img.style.visibility = "hidden"; };
    return img;
  }
  function claimFor(a) {
    /* Wording keyed by rule id — the brief carries numbers, never prose.
       Verbs calibrated: an imperative only when one panel's one number
       supports it outright; two measures print and the row stops. */
    const frag = document.createDocumentFragment();
    const P = i => (a.players || [])[i] || { name: String((a.codes || [])[i] ?? "?") };
    const n = a.numbers || {};
    const co = (t) => el("code", null, t);
    const add = (...parts) => parts.forEach(p =>
      frag.appendChild(typeof p === "string" ? document.createTextNode(p) : p));
    switch (a.rule) {
      case "availability":
        add(`${P(0).name} is flagged `, co(String(a.status || "?")));
        if (a.news) add(` — FPL says: `, el("q", null, a.news));
        add(" → he is in your 15; the drawer has the projections.");
        break;
      case "bench_inversion":
        add(`${P(0).name} projects `, co(fmt2(n.bench_xpts)),
            ` vs ${P(1).name} `, co(fmt2(n.starter_xpts)),
            ` starting → start ${P(0).name} over ${P(1).name} — `,
            co("+" + fmt2(n.swing)), " xPts, free.");
        break;
      case "captain_divergence": {
        const haul = P(0), mean = P(1), cap = P(2);
        add(`Captain: ${haul.name} by haul odds `,
            co(fmt1((n.haul_pick_p_haul ?? 0) * 100) + "%"),
            `, ${mean.name} by mean `,
            co(fmtSigned((n.mean_pick_xpts ?? 0) - (n.haul_pick_xpts ?? 0), 2)),
            ` — two measures, never blended. Armband: ${cap.name}.`);
        break;
      }
      case "own_price_fall":
        add(`${P(0).name} net `, co(fmtSigned(n.net)),
            ` in the ${fmt2(n.window_h)}h window (`,
            co(fmtSigned(n.net_per_hour) + "/hr"),
            `) — observed flow, not a predicted change.`);
        break;
      case "solve_stale":
        add("This plan solved for a deadline that has passed. ",
            a.reason || "");
        break;
      case "solve_missing":
        add(a.reason || "No stored solve for this season.");
        break;
      case "source_gap":
        add(`${a.source_panel} answered nothing: `, a.reason || "no reason given");
        break;
      default:
        add(a.rule, " ", JSON.stringify(n));
    }
    return frag;
  }
  function alertRow(a) {
    const row = el("div", "al p" + a.priority);
    row.appendChild(el("b", "al-kind", a.kind));
    if ((a.codes || []).length) row.appendChild(alertAvatar(a.codes[0]));
    const claim = el("span", "al-claim");
    claim.appendChild(claimFor(a));
    row.appendChild(claim);
    const cite = citeChip(a.source_panel, a.source_as_of);
    cite.onclick = () => drillTo(a.drill);
    row.appendChild(cite);
    if (a.drill) {
      row.classList.add("drillable");
      row.onclick = (e) => { if (e.target !== cite) drillTo(a.drill); };
    }
    return row;
  }
  function renderAlerts() {
    alertsBody.textContent = "";
    if (!brief) {
      alertsBody.appendChild(namedGap("dashboard_brief unavailable.",
        brR.ok ? String(brR.result.reason || "empty")
               : String(brR.error && brR.error.message || brR.error)));
      return;
    }
    const rows = brief.alerts || [];
    if (!rows.length) {
      alertsBody.appendChild(el("p", "db-quiet",
        `No flags on your 15 — squad checked at `
        + `${clockText(brief.sources_as_of?.squad_overview)}.`));
      return;
    }
    for (const a of rows) alertsBody.appendChild(alertRow(a));
  }

  /* ------------------------------------------------------------- pitch */
  renderPitch();

  function pjClass(x) {
    // bins beside the ramp docs in dashboard.css; anchor = XI median
    if (x == null || median == null) return "pj-n0";
    const d = x - median;
    if (d >= 1.5) return "pj-p2";
    if (d >= 0.5) return "pj-p1";
    if (d > -0.5) return "pj-n0";
    if (d > -1.5) return "pj-m1";
    return "pj-m2";
  }
  function riskOf(p) {
    // client-inferred from FPL status/news until the squad risk extension
    // ships; 'c' (creator-only concern) needs that extension and is not drawn
    if (!p.status || p.status === "a") return null;
    if (p.status === "d") return { letter: "d", cls: "d" };
    if (p.status === "i") return { letter: "i", cls: "i" };
    if (p.status === "s") return { letter: "s", cls: "s" };
    return { letter: p.status, cls: "i" };
  }
  function tierWord(x) {
    if (x == null || median == null) return "no projection";
    const d = x - median;
    if (d >= 1.5) return "well above XI median";
    if (d >= 0.5) return "above XI median";
    if (d > -0.5) return "at XI median";
    if (d > -1.5) return "below XI median";
    return "well below XI median";
  }
  function pcard(p) {
    const b = el("button", "pp" + (p.is_captain ? " cap" : ""));
    b.type = "button";
    if (p.team_code != null) b.dataset.club = String(p.team_code);
    b.setAttribute("aria-label",
      `${p.name}, ${p.pos}, ${fmtPrice(p.price)}, `
      + `${p.xpts != null ? fmt1(p.xpts) + " expected points" : "no projection"}, `
      + tierWord(p.xpts));

    const risk = riskOf(p);
    if (risk) {
      const r = el("span", "pp-risk " + risk.cls, risk.letter);
      r.title = (p.news ? `${p.news} ` : `status ${p.status} `) + "(FPL)";
      b.appendChild(r);
    }
    if (p.is_captain || p.is_vice)
      b.appendChild(el("span", "ribbon" + (p.is_vice ? " v" : ""),
                       p.is_captain ? "C" : "V"));

    const img = el("img", "pp-face");
    img.alt = ""; img.loading = "lazy"; img.decoding = "async";
    img.src = PHOTO(p.code);
    // clubmark discipline: one class flip, monogram underneath, zero reflow
    img.addEventListener("error", () => b.classList.add("fall"));
    b.appendChild(img);
    const mg = el("span", "pp-mg",
      String(p.name || "?").replace(/[^A-Za-zÀ-ž]/g, "").slice(0, 2).toUpperCase() || "?");
    mg.setAttribute("aria-hidden", "true");
    b.appendChild(mg);

    b.appendChild(el("span", "pp-nm", p.name));
    b.appendChild(el("span", "pp-sub", fmtPrice(p.price)));
    const chip = el("span", "pp-chip " + pjClass(p.xpts));
    chip.appendChild(document.createTextNode(
      p.xpts != null ? fmt1(p.xpts) : "–"));
    chip.appendChild(el("i", null, "xPts"));
    chip.title = p.xpts != null && median != null
      ? `${fmt2(p.xpts)} consensus xPts vs XI median ${fmt2(median)}`
      : "no cached projection";
    b.appendChild(chip);

    b.onclick = () => openDrawer(p.code);
    pitchCardByCode.set(p.code, b);
    return b;
  }
  function renderPitch() {
    pitchBody.textContent = "";
    if (!sq) {
      const why = sqR.ok
        ? String(sqR.result.reason || "empty")
        : String(sqR.error && sqR.error.message || sqR.error);
      pitchBody.appendChild(namedGap("No squad to draw.", why));
      return;
    }
    const pitch = el("div", "pitch");
    const byPos = { GKP: [], DEF: [], MID: [], FWD: [] };
    for (const p of sq.starters || []) (byPos[p.pos] || byPos.MID).push(p);
    for (const pos of ["GKP", "DEF", "MID", "FWD"]) {
      if (!byPos[pos].length) continue;
      const row = el("div", "row");
      byPos[pos].forEach(p => row.appendChild(pcard(p)));
      pitch.appendChild(row);
    }
    pitchBody.appendChild(pitch);
    const bench = el("div", "bench");
    (sq.bench || []).forEach((p, i) => {
      const slot = el("span", "bn-slot");
      slot.appendChild(el("i", "bn-i", String(i + 1)));
      slot.appendChild(pcard(p));
      bench.appendChild(slot);
    });
    pitchBody.appendChild(bench);

    const footLine = el("p", "sub");
    footLine.textContent =
      `source: ${sq.provenance_source}`
      + (sq.bank_tenths != null ? ` · bank ${fmtPrice(sq.bank_tenths / 10)}` : "")
      + (sq.projected_xi_xpts != null
          ? ` · XI ${fmt1(sq.projected_xi_xpts)} xPts (consensus)` : "")
      + (median != null ? ` · chip colour = vs your XI median ${fmt2(median)}` : "")
      + (sq.as_of ? ` · as of ${clockText(sq.as_of)}` : "");
    pitchBody.appendChild(footLine);
    pitchBody.appendChild(el("p", "sub db-legendnote",
      "GK and budget defenders sit below the median by construction — the "
      + "tier says who is cheap to upgrade, not who is failing."));
    for (const note of sq.notes || [])
      pitchBody.appendChild(el("p", "sub", note));
  }

  /* ------------------------------------------------------------- solver */
  renderSolver();
  function svFace(ref) {
    const side = el("span", "sv-side");
    const img = el("img", "sv-face");
    img.alt = ""; img.loading = "lazy";
    img.src = PHOTO(ref.code);
    img.addEventListener("error", () => side.classList.add("fall"));
    side.appendChild(img);
    const mg = el("span", "sv-mg",
      String(ref.name || "?").replace(/[^A-Za-zÀ-ž]/g, "").slice(0, 2).toUpperCase());
    if (ref.team_code != null) side.dataset.club = String(ref.team_code);
    side.appendChild(mg);
    const b = el("b", null, ref.name);
    side.appendChild(b);
    if (ref.price != null) side.appendChild(el("i", null, fmtPrice(ref.price)));
    return side;
  }
  function solverStatusChip(solverLine) {
    if (!solverLine) return null;
    const gap = /gap=([0-9.]+)/.exec(String(solverLine));
    const status = /status=([^g]+?)(?:\s+gap=|$)/.exec(String(solverLine));
    const chip = el("span", "chip warn");
    if (gap && status) {
      chip.textContent =
        `${status[1].trim().toLowerCase()} · within `
        + `${(parseFloat(gap[1]) * 100).toFixed(1)}% of proven optimal`;
    } else chip.textContent = String(solverLine);
    return chip;
  }
  function renderSolver() {
    solverCard.textContent = "";
    solverCard.appendChild(el("h2", null, "The solver would…"));
    const S = brief?.solve || null;
    const plan = planPayload?.plan || null;

    // header: the epistemology stated, solver units named, both clocks
    const sub = el("p", "sub");
    if (plan) {
      const h = plan.horizon_gws || [];
      sub.appendChild(document.createTextNode("it optimises "));
      sub.appendChild(el("code", null, String(plan.objective_mode || "?")));
      sub.appendChild(document.createTextNode(
        (h.length ? ` over GW${h[0]}–${h[h.length - 1]}` : "")
        + " and nothing else — it cannot see effective ownership, it never "
        + "banks a transfer, and it prices the future in "
        + `${(plan.n_sims || 0).toLocaleString()} simulations. `));
      if (plan.generated_at)
        sub.appendChild(el("span", "chip",
          `solved ${ageText(plan.generated_at)} ago`));
      if (plan.snapshot_as_of)
        sub.appendChild(el("span", "chip",
          `snapshot ${String(plan.snapshot_as_of).slice(0, 16).replace("T", " ")}`));
      const st = solverStatusChip(plan.solver);
      if (st) sub.appendChild(st);
    } else {
      sub.textContent = "the stored plan, its own units, its own confession.";
    }
    solverCard.appendChild(sub);

    // state machine — server-decided; the client only renders it
    if (!planR.ok) {
      solverCard.appendChild(namedGap("Solve plan unreachable.",
        String(planR.error && planR.error.message || planR.error)));
      return;
    }
    if (planPayload && planPayload.exists === false) {
      solverCard.appendChild(namedGap("No stored solve for this season.",
        `${planPayload.reason || ""} The pipeline writes one T-4h before `
        + `each deadline.`));
      const link = el("a", "chip", "open pipelines →");
      link.href = "#pipelines";
      solverCard.appendChild(link);
      return;
    }
    const state = S ? S.state : null;

    if (state === "stale" || (state == null && plan)) {
      // Today's real branch: the move is NOT rendered. A stale plan is a
      // prompt, never a silently stale recommendation.
      if (state === "stale") {
        const h = plan?.horizon_gws || [];
        const gen = plan?.generated_at
          ? String(plan.generated_at).slice(0, 10) : "?";
        solverCard.appendChild(namedGap(
          "This plan solved for a deadline that has passed.",
          `Generated ${gen} for GW${h[0] ?? "?"}–${h[h.length - 1] ?? "?"}; `
          + `the next deadline is GW${brief?.gw ?? planPayload.next_gw ?? "?"}. `
          + `Its moves were priced against a squad you no longer have.`));
      } else {
        solverCard.appendChild(namedGap(
          "Plan state unknown — dashboard_brief unavailable.",
          "The fresh/aging/stale decision runs server-side; showing the "
          + "artefact's own record only."));
      }
      const rerun = el("button", "chip", "Re-run solve");
      rerun.title = "POST /api/solve — the runner enforces one at a time";
      rerun.onclick = async () => {
        rerun.disabled = true; rerun.textContent = "solve queued…";
        try { await postJSON("/api/solve", { mode: "both" }); }
        catch (e) {
          rerun.textContent = "Re-run solve";
          rerun.disabled = false;
          solverCard.appendChild(errBox(e));
        }
      };
      solverCard.appendChild(rerun);
      // the plan's own metadata as a quiet historical record — objective in
      // the solver's own currency
      if (plan) {
        const rec = el("p", "sv-lines");
        rec.appendChild(document.createTextNode("record: objective "));
        rec.appendChild(el("code", null,
          `${fmt1(plan.objective)} (${plan.objective_mode})`));
        rec.appendChild(document.createTextNode(
          ` · horizon GW${(plan.horizon_gws || [])[0] ?? "?"}–`
          + `${(plan.horizon_gws || []).slice(-1)[0] ?? "?"}`
          + ` · ${plan.solver || ""}`));
        solverCard.appendChild(rec);
        renderChipAndCaptainLines(plan);
        renderConfession(plan);
      }
      return;
    }

    if (!plan) {
      solverCard.appendChild(namedGap("No plan payload.",
        "GET /api/solve/plan returned nothing renderable."));
      return;
    }

    if (state === "aging") {
      const chip = el("span", "chip warn",
        `aging — solved ${S.age_hours != null ? S.age_hours + "h" : "?"} ago, `
        + `before the T-${fmt1(thr.solve_fresh_window_h ?? 4)}h window`);
      solverCard.appendChild(el("p", "sv-lines")).appendChild(chip);
    }

    // Register 1 — the move (derived; the sub confesses the method)
    const derived = S?.derived;
    const moveBox = el("div", "sv-move");
    if (derived && derived.transfers.length) {
      for (const t of derived.transfers) {
        const strip = el("span", "sv-strip");
        const out = svFace(t.out); out.classList.add("out");
        const inn = svFace(t.in); inn.classList.add("in");
        strip.append(out, el("span", "sv-arrow", "→"), inn);
        if (t.price_delta != null)
          strip.appendChild(el("i", "sv-pd", fmtSigned(t.price_delta, 1)));
        moveBox.appendChild(strip);
      }
    } else if (derived) {
      moveBox.appendChild(el("span", null,
        "the plan keeps your current 15 — no moves derived"));
    } else {
      moveBox.appendChild(el("span", null,
        "moves underivable: squad or plan unreadable"));
    }
    // the gain slot: no hold_baseline stored -> a named gap, objective in
    // the solver's own currency beside it
    const gain = el("span", "sv-gain" + (S?.hold_baseline == null ? " gap" : ""));
    if (S?.hold_baseline != null) {
      gain.appendChild(document.createTextNode(
        `${fmtSigned(plan.objective - S.hold_baseline, 1)} objective `
        + `(${plan.objective_mode})`));
    } else {
      gain.appendChild(document.createTextNode(
        `objective ${fmt1(plan.objective)} (${plan.objective_mode})`));
      gain.appendChild(el("i", null,
        "no hold baseline stored; the gain of moving vs holding is a named "
        + "gap until the solve artefact carries one"));
    }
    moveBox.appendChild(gain);
    solverCard.appendChild(moveBox);
    if (derived) {
      solverCard.appendChild(el("p", "sub",
        "moves = plan vs your current 15 (derived — the artefact stores no "
        + "transfers[])"));
      if (derived.consensus_xpts_delta != null) {
        const line = el("p", "sv-lines");
        line.appendChild(document.createTextNode(
          `${derived.consensus_label || "consensus xPts for the swapped players"}: `));
        line.appendChild(el("code", null,
          `in ${fmt1(derived.consensus_xpts_in)} · out `
          + `${fmt1(derived.consensus_xpts_out)} · Δ `
          + `${fmtSigned(derived.consensus_xpts_delta, 1)}`));
        line.appendChild(document.createTextNode(
          " — the projection consensus voice, not the solver objective."));
        solverCard.appendChild(line);
      }
    }
    renderChipAndCaptainLines(plan);

    // Register 2 — the room: judgment rails, one line each, one source each
    const room = el("dl", "sv-room");
    const rail = (k, node) => {
      room.appendChild(el("dt", null, k));
      const dd = el("dd");
      if (typeof node === "string") dd.textContent = node;
      else dd.appendChild(node);
      room.appendChild(dd);
    };
    if (derived && derived.transfers.length) {
      const eo = document.createDocumentFragment();
      for (const t of derived.transfers) {
        eo.appendChild(document.createTextNode(
          `${t.in.name} ${t.in.own_pct != null ? fmt1(t.in.own_pct) + "%" : "?"}`
          + ` owned; ${t.out.name} `
          + `${t.out.own_pct != null ? fmt1(t.out.own_pct) + "%" : "?"} — `));
      }
      eo.appendChild(document.createTextNode(
        "shown per player, never netted. "));
      eo.appendChild(citeChip("FPL selected_by_pct (marginal own%)",
        brief?.sources_as_of?.squad_overview));
      rail("EO", eo);
      const price = document.createDocumentFragment();
      let anyFlow = false;
      for (const t of derived.transfers) {
        if (t.in_flow) {
          anyFlow = true;
          price.appendChild(document.createTextNode(
            `${t.in.name} ${fmtSigned(t.in_flow.net_per_hour)}/hr in · `));
        }
        if (t.out_flow) {
          anyFlow = true;
          price.appendChild(document.createTextNode(
            `${t.out.name} ${fmtSigned(t.out_flow.net_per_hour)}/hr out · `));
        }
      }
      if (anyFlow) {
        const wh = derived.transfers.find(t => t.in_flow || t.out_flow);
        const hours = (wh.in_flow || wh.out_flow).window_h;
        price.appendChild(document.createTextNode(
          `${hours != null ? hours + "h window" : ""} — observed transfer `
          + `flow, not a predicted price change. `));
        price.appendChild(citeChip("price_radar",
          brief?.sources_as_of?.price_radar));
        rail("Price", price);
      } else {
        rail("Price", "no flow recorded for the swapped players in the "
          + "current window — observed transfer flow only, never a "
          + "prediction.");
      }
    }
    solverCard.appendChild(room);
    solverCard.appendChild(kvNote("Bank.",
      "Holding banks a free transfer — the solver values this at zero by "
      + "construction. A decision, not a defect."));

    renderConfession(plan);

    const links = el("p", "sv-links");
    const l1 = el("a", "chip", "open planner →"); l1.href = "#planner";
    const l2 = el("a", "chip", "open the players →"); l2.href = "#xpoints";
    links.append(l1, l2);
    solverCard.appendChild(links);
    // no verdict, no accept button: the dashboard argues; the owner decides.
  }
  function renderChipAndCaptainLines(plan) {
    const S = brief?.solve || {};
    const lines = el("p", "sv-lines");
    const chip = S.chip ?? (plan.gw1 || {}).chip;
    if (chip) {
      lines.appendChild(document.createTextNode("chip: "));
      lines.appendChild(el("b", null, String(chip)));
      lines.appendChild(document.createTextNode(
        ` — the plan spends ${chip === "3xc" ? "triple captain" : chip}`
        + (S.chip_gw != null ? ` in GW${S.chip_gw}` : "")));
    } else {
      lines.appendChild(document.createTextNode("chip: none in this plan"));
    }
    const planCap = S.captain?.name
      ?? planPayload?.players?.[String((plan.gw1 || {}).captain)]?.name;
    const mine = S.your_captain?.name ?? sq?.captain;
    if (planCap) {
      lines.appendChild(document.createTextNode(
        ` · captain: ${planCap} (plan)`
        + (mine ? ` — you have: ${mine}${mine === planCap ? " ✓" : ""}` : "")));
    }
    solverCard.appendChild(lines);
  }
  function renderConfession(plan) {
    // Register 3 — the confession, verbatim, unparaphrased
    if (planPayload?.diff_lines?.length) {
      const d = el("p", "sv-diff");
      d.appendChild(document.createTextNode(
        planPayload.diff_lines.join(" · ") + " — objectives are choices "));
      const a = el("a", null, "→ solver tab");
      a.href = "#solver";
      d.appendChild(a);
      solverCard.appendChild(d);
    }
    if ((plan.notes || []).length) {
      const det = el("details", "sv-notes");
      det.appendChild(el("summary", null,
        "what the solver told us about itself"));
      for (const noteLine of plan.notes)
        det.appendChild(el("p", "mono", noteLine));
      solverCard.appendChild(det);
    }
  }

  /* -------------------------------------------------------------- tiles */
  renderTiles();
  function tileText(t) {
    /* claim / implication templates keyed by kind — every number is the
       payload's; a missing required arg throws (the tile contract). */
    const name = t.player?.name;
    const ctx = t.context || {};
    switch (t.kind) {
      case "xpts_standout":
        return {
          claim: `${name} projects ${fmt1(t.number.value)} over the window — `
            + `${ctx.weakest_starter} holds ${fmt1(ctx.weakest_starter_sum)}.`,
          imp: "→ a same-position upgrade path clears the printed margin.",
        };
      case "template_gap":
        return {
          claim: `${name} is owned by ${fmt1(t.number.value)}% of the game — `
            + `not by you.`,
          imp: "→ an unowned near-universal player is your largest "
            + "single-GW rank risk.",
        };
      case "differential":
        return {
          claim: `${name}: ${fmt1(t.number.value)} xPts next GW at `
            + `${fmt1(ctx.own_pct)}% owned.`,
          imp: `→ clears your XI median (${fmt2(ctx.xi_median)}) with the `
            + `field absent — two chips, two sources.`,
        };
      case "fixture_turn":
        return {
          claim: `${t.team}: ${String(ctx.axis || "").replace("_", " ")} `
            + `moves ${ctx.rank_near}→${ctx.rank_far} between windows.`,
          imp: "→ the run turns — timing context for moves involving "
            + `${t.team}.`,
        };
      case "price_rise_target":
        return {
          claim: `${name} net ${fmtSigned(t.number.value)}/hr inflow in the `
            + `${fmt2(t.number.window_h)}h window.`,
          imp: "→ a named target's flow is against waiting — flow, not a "
            + "prediction.",
        };
      case "idea_due":
        return {
          claim: `Open idea: ${ctx.subject} plays GW${Math.round(t.number.value)}.`,
          imp: "→ the registry expects an observation this gameweek.",
        };
      default:
        return { claim: `${t.kind}: ${fmt1(t.number.value)} ${t.number.unit}`,
                 imp: "" };
    }
  }
  function tileEl(t) {
    // required-args contract: a tile missing any leg throws rather than
    // rendering a number without its source
    for (const req of ["kind", "number", "gate", "source_panel"]) {
      if (t[req] == null) throw new Error(`tile missing required ${req}`);
    }
    const { claim, imp } = tileText(t);
    const a = el("a", "tile p" + t.priority);
    a.href = "javascript:void 0";
    a.appendChild(el("span", "t-claim", claim));
    const numEl = el("span", "t-num");
    numEl.appendChild(document.createTextNode(
      t.kind === "price_rise_target" ? fmtSigned(t.number.value)
        : fmt1(t.number.value)));
    numEl.appendChild(el("i", null,
      " " + t.number.unit
      + (t.number.window_h != null ? ` · ${fmt2(t.number.window_h)}h window` : "")));
    a.appendChild(numEl);
    if (imp) a.appendChild(el("span", "t-imp", imp));
    const meta = el("span", "t-meta");
    for (const s of (t.sources && t.sources.length
        ? t.sources : [{ panel: t.source_panel, as_of: t.source_as_of }]))
      meta.appendChild(citeChip(s.panel, s.as_of));
    meta.appendChild(el("span", "t-gate", "gate: " + t.gate));
    a.appendChild(meta);
    a.onclick = (e) => { e.preventDefault(); drillTo(t.drill); };
    return a;
  }
  function renderTiles() {
    tilesBody.textContent = "";
    if (!brief) {
      tilesBody.appendChild(namedGap("dashboard_brief unavailable.",
        "No gates were checked — this is a gap, not a quiet day."));
      return;
    }
    const tiles = brief.tiles || [];
    if (!tiles.length) {
      tilesBody.appendChild(el("p", "db-quiet",
        "Nothing cleared a gate this morning. The tabs have everything at "
        + "full depth."));
    } else {
      const grid = el("div", "tiles");
      for (const t of tiles) grid.appendChild(tileEl(t));
      tilesBody.appendChild(grid);
    }
    const sup = brief.suppressed_counts || {};
    const supN = Object.values(sup).reduce((a, b) => a + b, 0);
    if (supN > 0) {
      const kinds = Object.entries(sup)
        .map(([k, v]) => `${v} ${k}`).join(", ");
      tilesBody.appendChild(el("p", "sub",
        `+${supN} more cleared gates — suppressed (${kinds}). `
        + `The tabs have everything.`));
    }
    for (const e of brief.empty_kinds || [])
      tilesBody.appendChild(el("p", "sub db-emptykind",
        `${e.kind}: ${e.reason}`));
  }

  /* ---------------------------------------------------------- watch log */
  renderWatch();
  function renderWatch() {
    watchBody.textContent = "";
    if (!brief) {
      watchBody.appendChild(namedGap("The watch did not stand.",
        "dashboard_brief unavailable — no check ran, which is different "
        + "from every check coming back clear."));
      return;
    }
    const t = el("table", "data db-watch");
    const tb = el("tbody");
    for (const w of brief.watch_log || []) {
      const tr = el("tr", "w-" + w.status);
      tr.appendChild(el("td", "w-check", w.check.replace(/_/g, " ")));
      const st = el("td", "w-status");
      st.appendChild(el("span",
        "chip " + (w.status === "firing" ? "s1"
                   : w.status === "gap" ? "warn" : ""),
        w.status === "gap" ? "GAP" : w.status));
      tr.appendChild(st);
      tr.appendChild(el("td", "w-detail", w.detail));
      tr.appendChild(el("td", "w-src", w.source_panel));
      const ts = el("td", "w-asof num",
        w.as_of ? clockText(w.as_of) : "—");
      if (w.as_of) ts.title = w.as_of;
      tr.appendChild(ts);
      tb.appendChild(tr);
    }
    t.appendChild(tb);
    const wrap = el("div", "scroll-x");
    wrap.appendChild(t);
    watchBody.appendChild(wrap);
    watchBody.appendChild(el("p", "sub",
      "Absence of a tile above means checked-and-clear, never "
      + "didn't-look — that is what this table is for."));
  }

  /* ---------------------------------------------------------------- foot */
  {
    if (sqR.ok && sqR.prov) foot.appendChild(provenance(sqR.prov));
    if (brR.ok && brR.prov) foot.appendChild(provenance(brR.prov));
    if (brief) {
      const clocks = Object.entries(brief.sources_as_of || {})
        .map(([k, v]) => `${k} ${v ? clockText(v) : "?"}`).join(" · ");
      if (clocks) foot.appendChild(el("div", "provenance",
        "source clocks: " + clocks));
    }
  }
}
