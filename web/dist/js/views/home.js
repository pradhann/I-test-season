/* Dashboard — the front page, fplreview grammar.

   PAGE ORDER, exact: topbar stats + chip ledger → provenance banner → the
   AGENT BRIEFING (model-authored salience, clearly labelled, drawer drills
   only) → THE PITCH (suggested XI rendered by default, one toggle back to
   the locked picks; price-fall risk badges ride ON the cards) → the WATCH
   strip (own-player price risk, template gaps, rise targets — quiet rows
   below the squad, where they belong) → moves to consider (rule cards, not
   the solver) → the SOLVER CARD (the real transfer_plan move with avatars,
   gain labelled "solver forecast", live feedback while solving) → the
   rule-based signal tiles (cap 6, suppression disclosed) → the watch log
   folded to one line → foot.

   TWO VOICES, never merged: the agent Briefing is model-authored and says
   so in its provenance chip; the Signals tiles are deterministic gates.

   FOUR CALLS, in parallel: `squad_overview`, `dashboard_brief`,
   `GET /api/briefing`, `GET /api/solve/status` (read-only; a solve that
   survives a reload resumes its polling UI). Every number on this page
   comes from one of them; thresholds render from the brief's own
   `thresholds` echo — this file contains no gate constants. Wording lives
   HERE, keyed by rule/kind ids, because the brief carries no free-text
   recommendation field by contract.

   COLOUR LAW: each pitch card carries three chips. The xPts chip stays on
   the --s1/--s2 diverging ramp anchored at the XI median (bins in
   dashboard.css). The OPPONENT chip reuses the fixtures tab's fx- ramp on
   fixture_board's opponent_only ease — attack ease for MID/FWD, defence
   ease for GKP/DEF (a selection, never a blend), CAPS home / lower away,
   domain served by the payload. The MINUTES chip prints xmins when a
   provider serves it and the consensus appearance probability otherwise —
   labelled as which, never fabricated. --good/--warn/--bad stay reserved
   for the risk/status channel.

   SOLVER CURRENCY LAW: gain_over_roll is the solver's own forecast in the
   plan's objective_mode currency, labelled "solver forecast" on the card —
   it is never summed or blended with the consensus xPts on the pitch.

   Every zone degrades alone (tryPanel memo + named gaps); the page never
   blanks. */

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
function localClock(iso) {
  const d = parseTs(iso);
  if (!d) return "?";
  return d.toTimeString().slice(0, 5);
}
function fmtSigned(v, digits = 0) {
  if (v == null) return "–";
  const s = Math.abs(v).toLocaleString(undefined,
    { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return (v >= 0 ? "+" : "−") + s;
}

/* A section with no data says WHICH data and WHY — never whitespace. */
function namedGap(title, body) {
  const d = el("div", "fx-gap");
  d.appendChild(el("b", null, title));
  if (body instanceof Node) d.appendChild(body);
  else d.appendChild(document.createTextNode(body));
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

/* The fixtures tab's seven-class ease ramp, reused verbatim so the pitch's
   opponent chip and the fixtures grid agree by construction. */
const FX_CLASSES = ["fx-e3", "fx-e2", "fx-e1", "fx-n0", "fx-h1", "fx-h2", "fx-h3"];

const CHIP_NAME = { "3xc": "Triple Captain", bboost: "Bench Boost",
                    wildcard: "Wildcard", freehit: "Free Hit" };

/* ------------------------------------------------------------------ view */

export default async function home(host) {
  const dh = attachPlayerDrawer("home");

  const statsRow = el("div", "stats");
  const chipLedger = el("div", "db-chipledger");
  const provBanner = el("div", "db-prov");
  const intelCard = card(null, null);
  intelCard.classList.add("db-intel");
  const pitchCard = card("My squad", null);
  const pitchBody = el("div");
  pitchCard.appendChild(pitchBody);
  const watchStrip = el("div", "db-watchstrip");
  const movesCard = card("Moves to consider",
    "rule-based — not the solver; every gate echoed by the brief");
  const movesBody = el("div");
  movesCard.appendChild(movesBody);
  const solverCard = card(null, null);
  solverCard.classList.add("solver");
  const tilesCard = card("Signals",
    "rule-based voice — deterministic gates over the panels; the Briefing "
    + "above is the model's voice. Two voices, never merged.");
  const tilesBody = el("div");
  tilesCard.appendChild(tilesBody);
  const watchCard = card(null, null);
  const watchBody = el("div");
  watchCard.appendChild(watchBody);
  const foot = el("div", "db-foot");
  host.append(statsRow, chipLedger, provBanner, intelCard, pitchCard,
              watchStrip, movesCard, solverCard, tilesCard, watchCard, foot);

  const [sqR, brR, ibR, stR] = await Promise.all([
    tryPanel("squad_overview", {}),
    tryPanel("dashboard_brief", {}),
    getJSON("/api/briefing")
      .then(pl => ({ ok: true, payload: pl }))
      .catch(e => ({ ok: false, error: e })),
    getJSON("/api/solve/status")
      .then(pl => ({ ok: true, payload: pl }))
      .catch(e => ({ ok: false, error: e })),
  ]);
  const sq = sqR.ok && !sqR.result.empty ? sqR.result : null;
  let brief = brR.ok && !brR.result.empty ? brR.result : null;
  let intel = ibR.ok ? ibR.payload : null;
  let solveStatus = stR.ok ? stR.payload : null;
  const thr = brief?.thresholds || {};
  const median = brief?.xi_median_xpts ?? null;
  const suggested = brief?.suggested_xi || null;
  const teamFix = new Map(
    (brief?.team_fixtures || []).map(tf => [tf.team_code, tf]));
  const minutesBy = new Map(
    (brief?.squad_projection || []).map(m => [m.code, m]));
  const easeDom = (() => {
    const d = brief?.fixtures_scale?.domain;
    return Array.isArray(d) && d.length ? Math.abs(d[d.length - 1]) : null;
  })();

  function easeClass(ease) {
    // the fixtures view's bucket(): seven equal classes across the served
    // [-dom, +dom]; positive ease = easier = blue. No domain, no colour.
    if (ease == null || easeDom == null) return null;
    const s = Math.max(-1, Math.min(1, ease / easeDom));
    if (s >= 5 / 7) return FX_CLASSES[0];
    if (s >= 3 / 7) return FX_CLASSES[1];
    if (s >= 1 / 7) return FX_CLASSES[2];
    if (s > -1 / 7) return FX_CLASSES[3];
    if (s > -3 / 7) return FX_CLASSES[4];
    if (s > -5 / 7) return FX_CLASSES[5];
    return FX_CLASSES[6];
  }

  const pitchCardByCode = new Map();   // code -> .pp element (for drills)
  const playerIndex = new Map();       // code -> best-known player object
  const remember = r => { if (r && r.code != null) playerIndex.set(r.code, r); };
  if (sq) [...(sq.starters || []), ...(sq.bench || [])].forEach(remember);
  for (const a of brief?.alerts || []) (a.players || []).forEach(remember);
  for (const tl of brief?.tiles || []) remember(tl.player);
  for (const mv of brief?.moves || []) { remember(mv.in); remember(mv.out); }
  for (const mv of brief?.solve?.plan?.moves || []) {
    remember(mv.in); remember(mv.out);
  }
  remember(brief?.solve?.plan?.captain);

  // own-player price-fall risk rides ON the pitch card (the FLAGS strip is
  // dissolved; the squad is where own-player risk belongs)
  const dropByCode = new Map();
  for (const a of brief?.alerts || []) {
    if (a.rule === "own_price_fall")
      for (const c of a.codes || []) dropByCode.set(c, a.numbers || {});
  }

  function openDrawer(code) {
    const ref = playerIndex.get(code) || { code, name: String(code) };
    showPlayerDetail(dh, ref, {});
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

  function tinyFace(code) {
    const img = el("img", "avatar");
    img.loading = "lazy"; img.alt = "";
    img.src = PHOTO(code);
    img.onerror = () => { img.onerror = null; img.style.visibility = "hidden"; };
    return img;
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

  /* -------------------------------------------- topbar chip-status strip */
  {
    const SHORT = { wildcard: "WC", freehit: "FH", bboost: "BB", "3xc": "TC" };
    const gwNow = brief?.gw ?? null;
    if (sq && (sq.chips || []).length) {
      chipLedger.appendChild(el("span", "db-chiplbl", "chips"));
      for (const c of sq.chips) {
        const short = SHORT[c.chip] || String(c.chip).toUpperCase();
        const total = (c.windows || []).length;
        const left = Math.max(0, total - (c.played || []).length);
        const usedNow = gwNow != null && (c.windows || []).some(([lo, hi], i) =>
          gwNow >= lo && gwNow <= hi
          && (c.played || []).some(g => g >= lo && g <= hi));
        const chipEl = el("span",
          "db-chipitem" + (usedNow ? " used" : ""),
          `${short} ${left}/${total}`
          + ((c.played || []).length
              ? ` · used GW${c.played.join(", GW")}` : ""));
        chipEl.title = (c.windows || [])
          .map(([lo, hi], i) => {
            const playedIn = (c.played || []).filter(g => g >= lo && g <= hi);
            return `GW${lo}–${hi}: ${playedIn.length
              ? "used GW" + playedIn.join(", GW") : "available"}`;
          }).join(" · ");
        chipLedger.appendChild(chipEl);
      }
    } else if (sq) {
      chipLedger.appendChild(el("span", "db-chiplbl",
        "chips: unknown — this squad source serves no chip ledger"));
    }
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

  /* ----------------------------------------- the agent briefing (model) */
  renderIntel();
  function intelProvChip() {
    const chip = el("span", "db-agentchip",
      `agent-written · ${intel?.model || "?"} · `
      + (intel?.age_hours != null ? `${fmt1(intel.age_hours)}h`
                                  : ageText(intel?.generated_at)));
    chip.title = `model-authored briefing`
      + (intel?.generated_at ? ` · generated ${intel.generated_at}` : "")
      + (intel?.meta_prompt_hash
          ? ` · meta-prompt ${intel.meta_prompt_hash}` : "");
    return chip;
  }
  function generateBtn(label) {
    const btn = el("button", "chip", label || "Generate");
    btn.type = "button";
    btn.title = "POST /api/pipelines/briefing_intel/run — one model pass "
      + "over the panels; the artefact appears when the ledger settles";
    btn.onclick = () => generateIntel(btn);
    return btn;
  }
  function generateIntel(btn) {
    btn.disabled = true;
    btn.textContent = "generating…";
    const poll = () => {
      const started = Date.now();
      const t = setInterval(async () => {
        let st = null;
        try { st = await getJSON("/api/pipelines/briefing_intel/run_state"); }
        catch { return; }  // transient poll miss; try again in 5s
        const settled = st.state && st.state !== "running";
        const timedOut = Date.now() - started > 5 * 60 * 1000;
        if (!settled && !timedOut) {
          btn.textContent = "generating… (model pass running)";
          return;
        }
        clearInterval(t);
        try { intel = await getJSON("/api/briefing"); }
        catch (e) { intel = { empty: true, reason: String(e.message || e) }; }
        if (settled && st.state === "error" && intel?.empty)
          intel.reason = `briefing_intel run failed: ${st.detail || "no detail"}`;
        if (!settled && timedOut && intel?.empty)
          intel.reason = "still running after 5 min — the pipelines tab has "
            + "the ledger; this section will pick the artefact up on reload.";
        renderIntel();
      }, 5000);
    };
    postJSON("/api/pipelines/briefing_intel/run", {})
      .then(poll)
      .catch(e => {
        if (/HTTP 409/.test(String(e.message || e))) { poll(); return; }
        btn.disabled = false;
        btn.textContent = "Generate";
        btn.insertAdjacentElement("afterend",
          el("span", "db-quiet", ` trigger failed: ${e.message || e}`));
      });
  }
  function intelNumChip(n) {
    const v = Number.isInteger(n.value) ? String(n.value) : fmt1(n.value);
    const c = el("span", "cite", `${v}${n.unit} · ${n.source_panel}`);
    c.title = n.as_of ? `as of ${n.as_of}`
                      : `${n.source_panel}: no as-of instant served`;
    return c;
  }
  function intelItem(it) {
    const row = el("div", "ib-item sev" + it.severity);
    const main = el("div", "ib-main");
    main.appendChild(el("b", "ib-headline", it.headline));
    main.appendChild(el("p", "ib-why", it.why));
    const meta = el("span", "t-meta");
    for (const n of (it.numbers || []).slice(0, 4))
      meta.appendChild(intelNumChip(n));
    main.appendChild(meta);
    row.appendChild(main);
    const faces = el("span", "ib-faces");
    for (const c of (it.codes || []).slice(0, 3))
      faces.appendChild(tinyFace(c));
    row.appendChild(faces);

    // CLICK → the right-side drawer; a tab drill gets a drawer-less
    // in-place expansion. The briefing never navigates tabs.
    if (it.drill && it.drill.drawer != null) {
      row.onclick = () => openDrawer(it.drill.drawer);
    } else if (!it.drill && (it.codes || []).length) {
      row.onclick = () => openDrawer(it.codes[0]);
    } else {
      let detail = null;
      row.onclick = () => {
        if (detail) { detail.remove(); detail = null; return; }
        detail = el("div", "ib-detail");
        for (const n of it.numbers || []) {
          detail.appendChild(el("p", "ib-detailrow",
            `${n.value}${n.unit} — ${n.source_panel}`
            + (n.as_of ? ` · as of ${n.as_of}` : " · no as-of served")));
        }
        detail.appendChild(el("p", "ib-detailrow",
          `sources: ${(it.source_panels || []).join(", ") || "?"}`));
        main.appendChild(detail);
      };
    }
    return row;
  }
  function renderIntel() {
    intelCard.textContent = "";
    const head = el("div", "ib-head");
    head.appendChild(el("h2", null, "Briefing"));
    if (intel && !intel.empty) head.appendChild(intelProvChip());
    if (intel && !intel.empty && intel.inputs_moved) {
      const moved = el("span", "chip warn",
        "panels have moved since this was written");
      moved.title = "one or more input panels carries a newer as-of than "
        + "this briefing's generated_at";
      head.appendChild(moved);
      head.appendChild(generateBtn("Regenerate"));
    }
    intelCard.appendChild(head);

    if (!intel) {
      intelCard.appendChild(namedGap("Briefing unreachable.",
        String(ibR.error && ibR.error.message || ibR.error)));
      return;
    }
    if (intel.empty) {
      const gap = el("div", "ib-empty");
      gap.appendChild(el("p", "db-quiet",
        String(intel.reason || "no briefing artefact")));
      gap.appendChild(generateBtn());
      intelCard.appendChild(gap);
      return;
    }
    const items = [...(intel.items || [])]
      .sort((a, b) => (a.severity ?? 9) - (b.severity ?? 9));
    for (const it of items) intelCard.appendChild(intelItem(it));
    if (intel.rejected_n > 0)
      intelCard.appendChild(el("p", "sub",
        `${intel.rejected_n} candidate item(s) rejected by the citation `
        + `validator — dropped loudly, never silently.`));
  }

  /* -------------------- watch-strip wording (kept nested for the tests) */
  function claimFor(a) {
    /* Wording keyed by rule id — the brief carries numbers, never prose.
       Bench order and captaincy have no row here on purpose: they are
       applied in the suggested XI on the pitch. */
    const frag = document.createDocumentFragment();
    const P = i => (a.players || [])[i] || { name: String((a.codes || [])[i] ?? "?") };
    const n = a.numbers || {};
    const co = (txt) => el("code", null, txt);
    const add = (...parts) => parts.forEach(x =>
      frag.appendChild(typeof x === "string" ? document.createTextNode(x) : x));
    switch (a.rule) {
      case "availability":
        add(`${P(0).name} is flagged `, co(String(a.status || "?")));
        if (a.news) add(` — FPL says: `, el("q", null, a.news));
        break;
      case "own_price_fall":
        add(`${P(0).name} `, co(fmtSigned(n.net_per_hour) + "/hr"),
            ` in the ${fmt2(n.window_h)}h window (net `,
            co(fmtSigned(n.net)), `) — price watch, observed flow.`);
        break;
      case "solve_stale":
        add("solver: plan predates the deadline. ", a.reason || "");
        break;
      case "solve_missing":
        add("solver: ", a.reason || "no stored solve for this season.");
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
    const row = el("div", "al wl p" + a.priority);
    row.appendChild(el("b", "al-kind", a.kind));
    if ((a.codes || []).length) row.appendChild(tinyFace(a.codes[0]));
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
  function watchTileRow(t) {
    // template_gap / price_rise_target tiles rendered as quiet watch rows —
    // the brief's own numbers, filtered by kind, never re-derived
    const row = el("div", "al wl drillable");
    row.appendChild(el("b", "al-kind",
      t.kind === "template_gap" ? "TEMPLATE" : "MARKET"));
    if (t.player?.code != null) row.appendChild(tinyFace(t.player.code));
    const claim = el("span", "al-claim");
    if (t.kind === "template_gap") {
      claim.textContent = `${t.player?.name} ${fmt1(t.number.value)}% owned, `
        + `you don't — template gap.`;
    } else {
      claim.textContent = `${t.player?.name} ${fmtSigned(t.number.value)}/hr `
        + `inflow in the ${fmt2(t.number.window_h)}h window — rise watch.`;
    }
    row.appendChild(claim);
    row.appendChild(citeChip(t.source_panel, t.source_as_of));
    const code = t.player?.code ?? t.code;
    row.onclick = () => { if (code != null) openDrawer(code); };
    return row;
  }
  renderWatchStrip();
  function renderWatchStrip() {
    watchStrip.textContent = "";
    if (!brief) {
      watchStrip.appendChild(el("p", "db-quiet",
        "watch list unavailable — dashboard_brief did not answer."));
      return;
    }
    // solver/pipeline gap alerts: one slim utility row, never a banner
    const gaps = (brief.alerts || [])
      .filter(a => a.kind === "SOLVER" || a.kind === "GAP");
    if (gaps.length) {
      const util = el("div", "db-utilrow");
      for (const a of gaps.slice(0, 2)) {
        const span = el("span", "db-utilitem");
        span.appendChild(claimFor(a));
        span.onclick = () => drillTo(
          a.kind === "SOLVER" ? { focus: "solver" } : a.drill);
        util.appendChild(span);
      }
      watchStrip.appendChild(util);
    }
    // the Watch list: own price falls (alerts) + template gaps and rise
    // targets (tiles), max ~4 quiet rows
    const rows = [];
    for (const a of brief.alerts || [])
      if (a.rule === "own_price_fall") rows.push(alertRow(a));
    for (const t of brief.tiles || [])
      if (t.kind === "template_gap" || t.kind === "price_rise_target")
        rows.push(watchTileRow(t));
    if (!rows.length) return;
    const box = el("div", "db-watchlist");
    box.appendChild(el("span", "db-chiplbl", "watch"));
    for (const r of rows.slice(0, 4)) box.appendChild(r);
    if (rows.length > 4)
      box.appendChild(el("p", "db-quiet",
        `+${rows.length - 4} more in Signals below.`));
    watchStrip.appendChild(box);
  }

  /* ------------------------------------------------------------- pitch */
  const capDiffers = !!(suggested && suggested.captain && suggested.your_captain
    && suggested.captain.code !== suggested.your_captain.code);
  let pitchMode = (suggested && (suggested.n_changes > 0 || capDiffers))
    ? "suggested" : "picked";
  // per-render context read by pcard (which only ever reads squad fields
  // off its player argument)
  let capCodeCur = null;
  let viceCodeCur = null;
  let markByCode = new Map();

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
  function riskOf(status, news) {
    if (!status || status === "a") return null;
    if (status === "d") return { letter: "d", cls: "d" };
    if (status === "i") return { letter: "i", cls: "i" };
    if (status === "s") return { letter: "s", cls: "s" };
    return { letter: status, cls: "i" };
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
  function oppChip(teamCode, pos) {
    const tf = teamFix.get(teamCode);
    const chip = el("span", "pp-chip pp-opp");
    if (!tf || !tf.next) {
      chip.appendChild(document.createTextNode("–"));
      chip.appendChild(el("i", null, "opp"));
      chip.title = tf
        ? "blank gameweek — no fixture in the next round"
        : "no fixture data served for this club";
      return chip;
    }
    const nx = tf.next;
    const defensive = pos === "GKP" || pos === "DEF";
    const ease = defensive ? nx.defence_ease : nx.attack_ease;
    const kls = easeClass(ease);
    if (kls) chip.classList.add(kls);
    chip.appendChild(document.createTextNode(nx.label));
    chip.appendChild(el("i", null, nx.is_home ? "H" : "A"));
    chip.title = `${nx.opponent} (${nx.is_home ? "home" : "away"}) — `
      + (ease != null
          ? `${defensive ? "defence" : "attack"} ease ${fmtSigned(ease, 2)} `
            + `goals vs league average (opponent-only lens, fixture_board)`
          : (nx.unavailable || "no fitted rating for this fixture"))
      + (nx.attack_rank != null
          ? ` · ranks: attack ${nx.attack_rank}, defence ${nx.defence_rank} `
            + `(1 = easiest)` : "");
    return chip;
  }
  function minChip(code) {
    const m = minutesBy.get(code);
    const chip = el("span", "pp-chip pp-min");
    if (m && m.xmins != null) {
      chip.appendChild(document.createTextNode(String(Math.round(m.xmins))));
      chip.appendChild(el("i", null, "xmin"));
      chip.title = `${fmt1(m.xmins)} expected minutes — provider consensus `
        + `(${m.n_sources ?? "?"} source(s))`;
    } else if (m && m.p_appear != null) {
      chip.appendChild(document.createTextNode(
        `${Math.round(m.p_appear * 100)}%`));
      chip.appendChild(el("i", null, "appear"));
      chip.title = `${Math.round(m.p_appear * 100)}% to appear — provider `
        + `consensus (${m.n_sources ?? "?"} source(s)); no provider serves `
        + `expected minutes for this gameweek, so none are shown`;
    } else {
      chip.appendChild(document.createTextNode("–"));
      chip.appendChild(el("i", null, "min"));
      chip.title = "no provider minutes column for this player this gameweek";
    }
    return chip;
  }
  function pcard(p) {
    const isCap = capCodeCur != null && p.code === capCodeCur;
    const isVice = viceCodeCur != null && p.code === viceCodeCur;
    const b = el("button", "pp" + (isCap ? " cap" : ""));
    b.type = "button";
    if (p.team_code != null) b.dataset.club = String(p.team_code);
    b.setAttribute("aria-label",
      `${p.name}, ${p.pos}, ${fmtPrice(p.price)}, `
      + `${p.xpts != null ? fmt1(p.xpts) + " expected points" : "no projection"}, `
      + tierWord(p.xpts));

    const risk = riskOf(p.status, p.news);
    if (risk) {
      const r = el("span", "pp-risk " + risk.cls, risk.letter);
      r.title = (p.news ? `${p.news} ` : `status ${p.status} `) + "(FPL)";
      b.appendChild(r);
    }
    const drop = dropByCode.get(p.code);
    if (drop) {
      // price-fall risk: the own_price_fall alert's numbers, on the card
      const dEl = el("span", "pp-drop" + (risk ? " shift" : ""), "↓");
      dEl.title = `price-fall risk: net ${fmtSigned(drop.net)} in the `
        + `${fmt2(drop.window_h)}h window (${fmtSigned(drop.net_per_hour)}/hr)`
        + ` — observed flow, not a predicted change`;
      b.appendChild(dEl);
    }
    if (isCap || isVice)
      b.appendChild(el("span", "ribbon" + (isVice && !isCap ? " v" : ""),
                       isCap ? "C" : "V"));

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

    // ONE info row, three chips: xPts · opponent · minutes
    const rowEl = el("span", "pp-row");
    const xchip = el("span", "pp-chip " + pjClass(p.xpts));
    xchip.appendChild(document.createTextNode(
      p.xpts != null ? fmt1(p.xpts) : "–"));
    xchip.appendChild(el("i", null, "xPts"));
    xchip.title = p.xpts != null && median != null
      ? `${fmt2(p.xpts)} consensus xPts vs XI median ${fmt2(median)}`
      : "no cached projection";
    rowEl.appendChild(xchip);
    rowEl.appendChild(oppChip(p.team_code, p.pos));
    rowEl.appendChild(minChip(p.code));
    b.appendChild(rowEl);

    const mark = markByCode.get(p.code);
    if (mark) {
      const mk = el("span", "pp-mark", `⇄ for ${mark}`);
      mk.title = "suggested change vs your locked picks — toggle above to "
        + "see the squad as picked";
      b.appendChild(mk);
    }

    b.onclick = () => openDrawer(p.code);
    pitchCardByCode.set(p.code, b);
    return b;
  }
  renderPitch();
  function renderPitch() {
    pitchBody.textContent = "";
    pitchCardByCode.clear();
    if (!sq) {
      const why = sqR.ok
        ? String(sqR.result.reason || "empty")
        : String(sqR.error && sqR.error.message || sqR.error);
      pitchBody.appendChild(namedGap("No squad to draw.", why));
      return;
    }
    const all = [...(sq.starters || []), ...(sq.bench || [])];
    const byCode = new Map(all.map(x => [x.code, x]));
    const lockedCap = all.find(x => x.is_captain) || null;
    const lockedVice = all.find(x => x.is_vice) || null;
    const isSug = pitchMode === "suggested" && !!suggested;

    // header strip: the changes headline + the mode toggle
    if (suggested && (suggested.n_changes > 0 || capDiffers)) {
      const head = el("div", "db-pitchhead");
      const parts = [];
      if (suggested.n_changes > 0)
        parts.push(`${suggested.n_changes} change`
          + `${suggested.n_changes > 1 ? "s" : ""} vs your locked picks`
          + (suggested.total_delta_xpts != null
              ? `, ${fmtSigned(suggested.total_delta_xpts, 1)} xPts` : ""));
      else parts.push("your XI stands; the armband is the one suggestion");
      head.appendChild(el("span", "db-pitchdelta", parts.join(" ")));
      const tog = el("span", "db-toggle");
      const mk = (label, mode) => {
        const btn = el("button",
          "db-tog" + (pitchMode === mode ? " on" : ""), label);
        btn.type = "button";
        btn.onclick = () => { pitchMode = mode; renderPitch(); };
        return btn;
      };
      tog.append(mk("Suggested XI", "suggested"), mk("As picked", "picked"));
      head.appendChild(tog);
      pitchBody.appendChild(head);
    } else if (suggested && suggested.reason) {
      pitchBody.appendChild(el("p", "sub", suggested.reason));
    }

    const xiCodes = isSug && (suggested.xi_codes || []).length
      ? suggested.xi_codes
      : (sq.starters || []).map(x => x.code);
    const benchCodes = isSug && (suggested.bench_codes || []).length
      ? suggested.bench_codes
      : (sq.bench || []).map(x => x.code);
    capCodeCur = isSug && suggested.captain
      ? suggested.captain.code : (lockedCap ? lockedCap.code : null);
    viceCodeCur = lockedVice ? lockedVice.code : null;
    markByCode = new Map();
    if (isSug) {
      for (const s of suggested.swaps || []) {
        markByCode.set(s.in.code, s.out.name);   // on the pitch
        markByCode.set(s.out.code, s.in.name);   // on the bench
      }
    }

    const pitch = el("div", "pitch db-pitch2");
    const byPos = { GKP: [], DEF: [], MID: [], FWD: [] };
    for (const c of xiCodes) {
      const pl = byCode.get(c);
      if (pl) (byPos[pl.pos] || byPos.MID).push(pl);
    }
    for (const pos of ["GKP", "DEF", "MID", "FWD"]) {
      if (!byPos[pos].length) continue;
      const rowEl = el("div", "row");
      byPos[pos].forEach(pl => rowEl.appendChild(pcard(pl)));
      pitch.appendChild(rowEl);
    }
    pitchBody.appendChild(pitch);

    // the bench: a visually distinct tray below the pitch
    const tray = el("div", "bench db-benchtray");
    tray.appendChild(el("span", "db-benchlbl", "bench"));
    benchCodes.forEach((c, i) => {
      const pl = byCode.get(c);
      if (!pl) return;
      const slot = el("span", "bn-slot");
      slot.appendChild(el("i", "bn-i", String(i + 1)));
      slot.appendChild(pcard(pl));
      tray.appendChild(slot);
    });
    pitchBody.appendChild(tray);

    // the quiet armband note when the suggestion differs from the lock
    if (isSug && capDiffers) {
      pitchBody.appendChild(el("p", "sub db-armband",
        `your armband: ${suggested.your_captain.name}`
        + (suggested.captain_delta_xpts != null
            ? ` — suggested C ${suggested.captain.name} is `
              + `${fmtSigned(suggested.captain_delta_xpts, 1)} xPts by mean`
            : "")
        + (suggested.captain_by_haul
           && suggested.captain_by_haul.code !== suggested.captain.code
            ? ` · haul odds prefer ${suggested.captain_by_haul.name} — two `
              + `measures, never blended` : "")));
    }

    const footLine = el("p", "sub");
    footLine.textContent =
      `source: ${sq.provenance_source}`
      + (sq.bank_tenths != null ? ` · bank ${fmtPrice(sq.bank_tenths / 10)}` : "")
      + (sq.projected_xi_xpts != null
          ? ` · XI ${fmt1(sq.projected_xi_xpts)} xPts (consensus)` : "")
      + (median != null ? ` · xPts chip colour = vs your XI median ${fmt2(median)}` : "")
      + (easeDom != null
          ? ` · opponent chip colour = fixture ease (fixtures tab's ramp)` : "")
      + (sq.as_of ? ` · as of ${clockText(sq.as_of)}` : "");
    pitchBody.appendChild(footLine);
    pitchBody.appendChild(el("p", "sub db-legendnote",
      "GK and budget defenders sit below the median by construction — the "
      + "tier says who is cheap to upgrade, not who is failing."));
    for (const note of sq.notes || [])
      pitchBody.appendChild(el("p", "sub", note));
  }

  /* --------------------------------------------------- moves to consider */
  renderMoves();
  function moveFace(ref, side) {
    const wrap = el("span", "sv-side " + side);
    const img = el("img", "sv-face");
    img.alt = ""; img.loading = "lazy";
    img.src = PHOTO(ref.code);
    img.addEventListener("error", () => wrap.classList.add("fall"));
    wrap.appendChild(img);
    const mg = el("span", "sv-mg",
      String(ref.name || "?").replace(/[^A-Za-zÀ-ž]/g, "").slice(0, 2).toUpperCase());
    if (ref.team_code != null) wrap.dataset.club = String(ref.team_code);
    wrap.appendChild(mg);
    wrap.appendChild(el("b", null, ref.name));
    if (ref.price != null) wrap.appendChild(el("i", null, fmtPrice(ref.price)));
    return wrap;
  }
  function gwsText(gws) {
    if (!gws || !gws.length) return "?";
    return gws.length > 1 ? `${gws[0]}–${gws[gws.length - 1]}` : String(gws[0]);
  }
  function moveSentence(mv) {
    /* Fixed templates keyed by rule id — every number is the payload's. */
    const n = mv.numbers || {};
    if (mv.rule === "coverage_gap") {
      return `${mv.team} has the #${n.attack_rank} easiest attacking run and `
        + `you hold ${n.held_count}. ${mv.in.name} — ${fmt1(n.cand_xpts)} xPts `
        + `GW${n.next_gw}, ${n.cand_goals}G+${n.cand_assists}A in `
        + `GW${gwsText(mv.gws)} — fits for ${mv.out.name} `
        + `(${fmt1(n.out_xpts)} xPts).`;
    }
    if (mv.rule === "form_upgrade") {
      return `${mv.in.name} beats ${mv.out.name} on both gates: `
        + `${n.cand_returns} vs ${n.out_returns} returns in `
        + `GW${gwsText(mv.gws)} (gate ≥ +${thr.form_returns_margin ?? "?"}) `
        + `and ${fmt1(n.cand_xpts)} vs ${fmt1(n.out_xpts)} xPts `
        + `GW${n.next_gw} (gate ≥ +${fmt1(thr.form_xpts_margin)}).`;
    }
    return `${mv.rule}: ${mv.out.name} → ${mv.in.name}`;
  }
  function moveEl(mv) {
    const n = mv.numbers || {};
    const box = el("div", "db-move");
    const strip = el("span", "sv-strip");
    strip.append(moveFace(mv.out, "out"),
                 el("span", "sv-arrow", "→"),
                 moveFace(mv.in, "in"));
    box.appendChild(strip);
    box.appendChild(el("p", "db-movewhy", moveSentence(mv)));
    if (n.in_price != null && n.out_price != null) {
      box.appendChild(el("p", "db-movemath",
        `${fmtPrice(n.out_price)} sale + ${fmtPrice(n.bank ?? 0)} bank covers `
        + `${fmtPrice(n.in_price)}` + (
          n.out_price + (n.bank ?? 0) - n.in_price >= 0
            ? ` (${fmtPrice(n.out_price + (n.bank ?? 0) - n.in_price)} spare)`
            : "")));
    }
    const meta = el("span", "t-meta");
    for (const s of mv.sources || [])
      meta.appendChild(citeChip(s.panel, s.as_of));
    meta.appendChild(el("span", "t-gate", "rule-based — not the solver"));
    box.appendChild(meta);
    box.onclick = () => drillTo(mv.drill);
    box.classList.add("drillable");
    return box;
  }
  function renderMoves() {
    movesBody.textContent = "";
    if (!brief) {
      movesBody.appendChild(namedGap("dashboard_brief unavailable.",
        "No move rule ran — this is a gap, not a quiet day."));
      return;
    }
    const list = brief.moves || [];
    if (!list.length) {
      const gapNote = (brief.empty_kinds || [])
        .find(e => e.kind === "moves");
      movesBody.appendChild(el("p", "db-quiet",
        gapNote ? `No move cards: ${gapNote.reason}`
                : "No candidate cleared the coverage or form gates today."));
      return;
    }
    for (const mv of list) movesBody.appendChild(moveEl(mv));
    if (brief.moves_suppressed > 0)
      movesBody.appendChild(el("p", "sub",
        `+${brief.moves_suppressed} more cleared the gates — suppressed at `
        + `the served cap of ${thr.move_cap ?? "?"}.`));
  }

  /* ------------------------------------------------------------- solver */
  let solveKicked = false;
  let solvePollTimer = null;
  let solveTickerEl = null;
  renderSolver();
  if (solveStatus && solveStatus.state === "running") startSolvePolling();

  function fullDetailLink() {
    const a = el("a", "chip", "full detail → Solver tab");
    a.href = "#solver";
    return a;
  }
  function lastLogLine() {
    const t = solveStatus?.log_tail || [];
    for (let i = t.length - 1; i >= 0; i--) {
      const s = String(t[i]).trim();
      if (s) return s;
    }
    return "";
  }
  function startSolvePolling() {
    if (solvePollTimer) return;
    solvePollTimer = setInterval(async () => {
      let st;
      try { st = await getJSON("/api/solve/status"); }
      catch { return; }   // transient poll miss; the next tick retries
      solveStatus = st;
      if (st.state === "running") {
        if (solveTickerEl) solveTickerEl.textContent = lastLogLine();
        return;
      }
      clearInterval(solvePollTimer);
      solvePollTimer = null;
      if (st.state === "done") {
        // the artefact changed on disk: refetch the brief, re-render the
        // card (and the watch strip, whose solver gap row may have cleared)
        const r = await tryPanel("dashboard_brief", {});
        if (r.ok && !r.result.empty) brief = r.result;
      }
      renderSolver();
      renderWatchStrip();
    }, 5000);
  }
  function rerunButton(prominent) {
    const b = el("button", "chip" + (prominent ? " sv-rerun" : ""),
      "Re-run solve");
    b.type = "button";
    b.title = "POST /api/solve mode=transfers — runs `fpl recommend` against "
      + "your current 15 and commits a new plan (~2–5 min)";
    b.onclick = async () => {
      b.disabled = true;
      b.textContent = "starting…";
      try {
        await postJSON("/api/solve", { mode: "transfers" });
        solveKicked = true;
        solveStatus = { state: "running",
                        started_utc: new Date().toISOString(), log_tail: [] };
        renderSolver();
        startSolvePolling();
      } catch (e) {
        b.disabled = false;
        b.textContent = "Re-run solve";
        b.insertAdjacentElement("afterend", errBox(e));
      }
    };
    return b;
  }
  function solveRunningEl() {
    const box = el("div", "sv-running");
    const line = el("p", "sv-runline");
    line.appendChild(el("span", "sv-spin"));
    line.appendChild(document.createTextNode(
      `Solving… started ${localClock(solveStatus?.started_utc)} — `
      + `typically 2–5 min`));
    box.appendChild(line);
    solveTickerEl = el("p", "sv-ticker", lastLogLine());
    box.appendChild(solveTickerEl);
    return box;
  }
  function renderSolver() {
    solverCard.textContent = "";
    solveTickerEl = null;
    const S = brief?.solve || null;
    const plan = S?.plan || null;
    const running = solveStatus?.state === "running";

    const head = el("div", "sv-head");
    head.appendChild(el("h2", null, "Solver — your move"));
    if (S && S.state !== "missing") {
      const cls = S.state === "fresh" ? " s1"
                : S.state === "aging" ? " warn" : " bad";
      const chip = el("span", "chip" + cls,
        S.state + (S.age_hours != null ? ` · ${fmt1(S.age_hours)}h old` : ""));
      chip.title = S.generated_at
        ? `plan generated ${S.generated_at}` : "no generated_at on the plan";
      head.appendChild(chip);
    }
    solverCard.appendChild(head);

    if (!brief) {
      solverCard.appendChild(namedGap("Solve state unknowable.",
        "dashboard_brief unavailable — the solve block rides in it."));
    } else if (S.state === "missing") {
      solverCard.appendChild(namedGap("No transfer plan artefact.",
        S.reason || "no stored solve for this season."));
    } else if (S.state === "stale") {
      // honest: a stale plan's moves were priced against a squad you no
      // longer have — the brief serves no plan body for it, on purpose
      solverCard.appendChild(el("p", "sv-stale",
        (S.reason || "this plan solved for a deadline that has passed — "
         + "its moves were priced against a squad you no longer have.")
        + (S.generated_at ? ` (generated ${ageText(S.generated_at)} ago)` : "")));
    } else if (!plan) {
      solverCard.appendChild(namedGap("Plan body absent.",
        `solve state is "${S.state}" but the brief served no plan payload — `
        + `a backend gap, not a quiet day.`));
    } else {
      const h = plan.horizon_gws || [];
      const hSpan = `GW${h[0] ?? "?"}–${h[h.length - 1] ?? "?"}`;

      if (plan.is_roll) {
        // banking the transfer IS the recommendation — a positive card
        const roll = el("div", "sv-roll");
        roll.appendChild(el("b", "sv-rollhead", "Bank the transfer"));
        roll.appendChild(el("p", "sv-why",
          `The solved recommendation over ${hSpan}: no move cleared the bar `
          + `vs rolling`
          + (plan.free_transfers != null
              ? ` — you carry ${plan.free_transfers} free transfer(s) forward.`
              : ".")));
        solverCard.appendChild(roll);
      } else if ((plan.moves || []).length) {
        const box = el("div", "sv-move sv-planmoves");
        for (const mv of plan.moves) {
          const row = el("div", "sv-moverow");
          const strip = el("span", "sv-strip");
          strip.append(moveFace(mv.out, "out"),
                       el("span", "sv-arrow", "→"),
                       moveFace(mv.in, "in"));
          row.appendChild(strip);
          const bits = [];
          if (mv.price_delta != null)
            bits.push(`price ${fmtSigned(mv.price_delta, 1)}`);
          if (mv.out_flow)
            bits.push(`${mv.out.name} flow `
              + `${fmtSigned(mv.out_flow.net_per_hour)}/hr`
              + (mv.out_flow.window_h != null
                  ? ` (${fmt1(mv.out_flow.window_h)}h)` : ""));
          if (mv.in_flow)
            bits.push(`${mv.in.name} flow `
              + `${fmtSigned(mv.in_flow.net_per_hour)}/hr`
              + (mv.in_flow.window_h != null
                  ? ` (${fmt1(mv.in_flow.window_h)}h)` : ""));
          if (bits.length)
            row.appendChild(el("p", "sv-flowline", bits.join(" · ")));
          box.appendChild(row);
        }
        solverCard.appendChild(box);
      } else {
        solverCard.appendChild(el("p", "sub",
          "the plan names no paired moves — the Solver tab has the raw sets."));
      }

      // the gain, in the solver's own currency, labelled as such
      if (!plan.is_roll && plan.gain_over_roll != null) {
        const line = el("p", "sv-gainline");
        line.appendChild(el("b", null,
          `${fmtSigned(plan.gain_over_roll, 1)} xPts`));
        line.appendChild(document.createTextNode(
          ` over ${hSpan} vs rolling — solver forecast`
          + (plan.free_transfers != null
              ? ` · ${plan.free_transfers} free transfer(s)` : "")));
        line.title = "the solver's own forecast in its own currency ("
          + (plan.objective_mode || "?")
          + ") — never blended with the consensus xPts on the pitch";
        solverCard.appendChild(line);
      }

      // hits, only when the plan actually spends points
      if ((plan.hits ?? 0) > 0) {
        const v = plan.hit_verdict;
        const line = el("p", "sv-lines");
        line.appendChild(document.createTextNode(
          `hits: ${plan.hits} (−${plan.hit_points ?? plan.hits * 4} pts)`));
        if (v && v.justified != null) {
          line.appendChild(el("span",
            "chip " + (v.justified ? "s1" : "warn"),
            v.justified ? "justified" : "not justified"));
          if (v.expected_gain != null && v.breakeven_gain != null)
            line.appendChild(document.createTextNode(
              ` — expected ${fmt1(v.expected_gain)} vs breakeven `
              + `${fmt1(v.breakeven_gain)}`));
        }
        solverCard.appendChild(line);
      }

      // the chip, when the plan spends one
      if (plan.chip) {
        const line = el("p", "sv-lines");
        line.appendChild(el("span", "chip s1",
          CHIP_NAME[plan.chip] || String(plan.chip)));
        line.appendChild(document.createTextNode(
          ` — the plan spends it`
          + (plan.gw != null ? ` in GW${plan.gw}` : "")));
        solverCard.appendChild(line);
      }

      // the armband, with the avatar; yours printed only when it differs
      if (plan.captain) {
        const line = el("p", "sv-capline");
        line.appendChild(tinyFace(plan.captain.code));
        const differs = plan.your_captain
          && plan.your_captain.code !== plan.captain.code;
        line.appendChild(document.createTextNode(
          `Solver captain: ${plan.captain.name}`
          + (differs ? ` — yours: ${plan.your_captain.name}` : "")));
        solverCard.appendChild(line);
      }

      // the losing alternatives, one collapsed line
      const alts = plan.alternatives || [];
      if (alts.length) {
        const det = el("details", "sv-alts");
        det.appendChild(el("summary", null,
          "beat: " + alts.map(a => a.summary).join(" · ")));
        for (const a of alts) {
          det.appendChild(el("p", "sv-altrow",
            a.summary
            + (a.objective != null
                ? ` — ${fmt1(a.objective)} ${plan.objective_mode || ""}`
                  + ` (solver currency)` : "")
            + (a.hits ? ` · ${a.hits} hit(s)` : "")));
        }
        solverCard.appendChild(det);
      }

      // the solver's own confessions
      if ((plan.notes || []).length || plan.bounds) {
        const det = el("details", "sv-notes");
        det.appendChild(el("summary", null, "solver notes & bounds"));
        for (const n of plan.notes || [])
          det.appendChild(el("p", "mono", n));
        if (plan.bounds) det.appendChild(el("p", "mono", plan.bounds));
        solverCard.appendChild(det);
      }
    }

    const controls = el("div", "sv-controls");
    if (running) {
      controls.appendChild(solveRunningEl());
    } else {
      const prominent = !S || S.state === "stale" || S.state === "missing";
      controls.appendChild(rerunButton(prominent));
      if (solveKicked && solveStatus
          && (solveStatus.state === "failed" || solveStatus.state === "error"))
        controls.appendChild(el("p", "sv-ticker sv-fail",
          "solve failed — " + (lastLogLine() || "no log tail served")));
      controls.appendChild(fullDetailLink());
    }
    solverCard.appendChild(controls);
    // no accept button: the dashboard argues; the owner decides.
  }

  /* -------------------------------------------------------------- tiles */
  renderTiles();
  function tileText(t) {
    /* claim / implication templates keyed by kind — every number is the
       payload's; a missing required arg throws (the tile contract).
       Unknown kinds fall to the default template on purpose: the idea
       registry left briefings, and any future kind degrades honestly. */
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
    for (const e of brief.empty_kinds || []) {
      if (e.kind === "moves") continue;   // rendered under Moves to consider
      tilesBody.appendChild(el("p", "sub db-emptykind",
        `${e.kind}: ${e.reason}`));
    }
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
    const rows = brief.watch_log || [];
    const counts = { clear: 0, firing: 0, gap: 0 };
    for (const w of rows) counts[w.status] = (counts[w.status] || 0) + 1;
    const newest = rows.map(w => w.as_of).filter(Boolean).sort().pop() || null;

    // one collapsed line; the full table lives behind the click
    const det = el("details", "db-watchfold");
    const sum = el("summary", null,
      `${rows.length} checks · ${counts.clear} clear`
      + (counts.firing ? ` · ${counts.firing} firing` : "")
      + (counts.gap ? ` · ${counts.gap} gap` : "")
      + (newest ? ` · ${clockText(newest)}` : ""));
    det.appendChild(sum);

    const tbl = el("table", "data db-watch");
    const tb = el("tbody");
    for (const w of rows) {
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
    tbl.appendChild(tb);
    const wrap = el("div", "scroll-x");
    wrap.appendChild(tbl);
    det.appendChild(wrap);
    det.appendChild(el("p", "sub",
      "Absence of a signal above means checked-and-clear, never "
      + "didn't-look — that is what this table is for."));
    watchBody.appendChild(det);
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
