/* Projections (#xpoints): the projection matrix (fplreview idiom, designed to be operated).
   One mental model: pick WHOSE numbers (source chips, multi-select), pick
   WHICH gameweeks (toggleable GW chips), then read a matrix where every
   column header sorts. Squad membership is a quiet dot, not a shout. */

import { runPanel, el, emptyBox, errBox, provenance,
         faceImg, fmtPrice, fmt1, fmt2, fmtSpan } from "/js/app.js";
// the SHARED player drawer: per-source pivot, percentile pizza, Understat
// profile and the chatter strip — the same surface the dashboard opens
import { attachPlayerDrawer, showPlayerDetail } from "/js/components/playerdrawer.js";

export default async function xpoints(host) {
  const card = el("section", "card");
  card.appendChild(el("h2", null, "Projections"));
  card.appendChild(el("p", "sub",
    "Numbers are copied from ingested providers, never modelled here. Feeds " +
    "refresh T-30h before each deadline and nightly; a newly ingested " +
    "provider appears below automatically."));
  const srcRow = el("div", "toolbar");
  const wRow = el("div", "toolbar");       // consensus weighting toggle
  const wBox = el("div", "wbox");          // weights table + accuracy strip
  // ONE closed disclosure holds the toggle, the note, the weights table and
  // the accuracy strip. The summary states the finding in one line; the
  // evidence shows only after a click. Open state survives re-renders
  // because only the children are rewritten, never the element.
  const wDet = el("details", "wdet");
  const wSum = el("summary", "sub");
  wSum.style.cursor = "pointer";
  wDet.append(wSum, wRow, wBox);
  const gwRow = el("div", "toolbar");
  const filterRow = el("div", "toolbar");
  const body = el("div");
  const foot = el("div");
  const dh = attachPlayerDrawer("xpoints");
  card.append(srcRow, wDet, gwRow, filterRow, body, foot);
  host.appendChild(card);

  // ---- state ----
  let picked = new Set();          // sources; empty = all (full consensus)
  let gwSel = new Set();           // chosen gameweeks; filled after first load
  let pos = "", team = "", search = "", maxPrice = "", minPapp = "";
  let squadOnly = false, showAccuracy = false, accScope = "own_gt5";
  // "equal" is the default on purpose: three settled gameweeks is a thin
  // track record. The panel names which blend drove every block; the UI
  // repeats that name rather than assuming the request was honoured.
  let weighting = "equal";         // "equal" | "earned"
  let sortBy = { kind: "sum" };    // {kind:"gw",gw} | {kind:"col",key} | {kind:"sum"}
  let sortDir = -1;                // -1 desc
  let res = null, squadCodes = new Set();

  runPanel("squad_overview", {}).then(({ result }) => {
    if (result && !result.empty) {
      squadCodes = new Set(
        [...(result.starters || []), ...(result.bench || [])].map(p => p.code));
      if (res) renderBody();
    }
  }).catch(() => {});

  async function fetchPanel() {
    body.textContent = "";
    body.appendChild(el("p", "sub", "loading…"));
    const gws = [...gwSel].sort((a, b) => a - b);
    const anchor = gws.length ? gws[0] : "next";
    const span = gws.length ? Math.min(8, gws[gws.length - 1] - gws[0] + 1) : 8;
    const params = { gw: anchor, span, limit: 200, weighting };
    if (picked.size) params.sources = [...picked];
    if (pos) params.position = { GKP: 1, DEF: 2, MID: 3, FWD: 4 }[pos];
    if (team) params.team = team;
    if (maxPrice) params.max_price = Number(maxPrice);
    if (minPapp) params.min_p_appear = Number(minPapp);
    try {
      const { result, provenance: prov } = await runPanel("projection_table", params);
      res = result;
      foot.textContent = "";
      foot.appendChild(provenance(prov));
      if (res.empty) {
        renderSources(); renderWeighting(); body.textContent = "";
        body.appendChild(emptyBox(res.reason)); return;
      }
      if (!gwSel.size)
        gwSel = new Set((res.gws || []).slice(0, 5));
      renderSources(); renderWeighting(); renderGws(); renderFilters(); renderBody();
    } catch (e) { body.textContent = ""; body.appendChild(errBox(e)); }
  }

  // ---- row 1: sources (multi-select chips with freshness) ----
  function ageInfo(iso) {
    const h = (Date.now() - new Date(iso.replace(" ", "T"))) / 3.6e6;
    if (!isFinite(h)) return { cls: "bad", text: "?" };
    if (h < 36) return { cls: "good", text: h < 1.5 ? "fresh" : fmtSpan(h) };
    if (h < 72) return { cls: "warn", text: fmtSpan(h) };
    return { cls: "bad", text: fmtSpan(h) };
  }
  function renderSources() {
    srcRow.textContent = "";
    const metas = res?.source_meta || [];
    srcRow.appendChild(el("span", "tlabel", "Sources"));
    const allChip = el("button", "chip src" + (picked.size === 0 ? " on" : ""));
    allChip.textContent = (picked.size === 0 ? "✓ " : "") + "All";
    allChip.title = "consensus across every provider";
    allChip.onclick = () => { picked.clear(); fetchPanel(); };
    srcRow.appendChild(allChip);
    const accBy = {};
    for (const acc of res?.accuracy || [])
      if (acc.scope === "own_gt5") accBy[acc.provider] = acc;
    const shown = [...gwSel];
    for (const m of metas) {
      const covers = !shown.length
        || shown.some(g => g >= m.gw_min && g <= m.gw_max);
      const on = picked.has(m.source);
      const a = ageInfo(m.last_fetched);
      const chip = el("button",
        "chip src" + (on ? " on" : "") + (covers ? "" : " off"));
      chip.appendChild(document.createTextNode((on ? "✓ " : "")));
      chip.appendChild(el("span", "freshdot " + a.cls));
      chip.appendChild(document.createTextNode(
        ` ${m.source.replace(/^gh_/, "")} · ${a.text}`));
      const acc = accBy[m.source];
      chip.title =
        `covers GW${m.gw_min}–${m.gw_max} · ${m.n_rows.toLocaleString()} rows` +
        (m.has_p_appear ? " · p(appear)" : "") + (m.has_xmins ? " · xMins" : "") +
        (acc && acc.mae != null
          ? `\nMAE ${acc.mae} on >5%-owned players · earned weight ${acc.weight}`
          : "") +
        `\nlast fetch ${m.last_fetched}` +
        (covers ? "\nclick to include/exclude"
                : "\nNO DATA for the selected gameweeks");
      if (covers) chip.onclick = () => {
        picked.has(m.source) ? picked.delete(m.source) : picked.add(m.source);
        fetchPanel();
      };
      else chip.disabled = true;
      srcRow.appendChild(chip);
    }
    const n = (res?.active_sources || res?.sources || []).length;
    srcRow.appendChild(el("span", "sub",
      picked.size ? `consensus of ${n} selected` : `consensus of all ${n}`));
    if ((res?.accuracy || []).some(a => a.mae != null)) {
      const acc = el("button", "chip" + (showAccuracy ? " s1" : ""),
                     "measured accuracy");
      acc.title = "each provider scored against settled gameweek actuals";
      acc.onclick = () => { showAccuracy = !showAccuracy; renderBody(); };
      srcRow.appendChild(acc);
    }
  }

  // ---- row 1b: consensus weighting (labelled toggle, default equal) ----
  const shortName = s => String(s).replace(/^gh_/, "");
  const fitStamp = iso => String(iso || "").replace("T", " ").slice(0, 16);
  const deltaChip = (v, base) => {
    // negative delta = provider's MAE below the consensus baseline = better
    if (v == null || base == null) return null;
    const d = v - base;
    const c = el("span", "accd " + (d <= 0 ? "good" : "bad"),
                 (d > 0 ? "+" : "") + fmt2(d));
    c.title = `${fmt2(v)} vs the equal-weight consensus ${fmt2(base)} ` +
              `on the same players`;
    return c;
  };

  function renderWeighting() {
    wRow.textContent = ""; wBox.textContent = "";
    const lbl = el("span", "tlabel", "Consensus");
    lbl.id = "xp-weighting-label";
    wRow.appendChild(lbl);
    const seg = el("span", "seg wseg");
    seg.setAttribute("role", "group");
    seg.setAttribute("aria-labelledby", "xp-weighting-label");
    for (const [k, label, tip] of [
      ["equal", "equal weights",
       "every provider counts the same (sem_projection_consensus)"],
      ["earned", "earned weights",
       "inverse-MSE weights fitted on settled gameweeks " +
       "(sem_projection_consensus_weighted); a provider that trails the " +
       "consensus earns less than an equal share"],
    ]) {
      const b = el("button", k === weighting ? "on" : "", label);
      b.title = tip;
      b.setAttribute("aria-pressed", String(k === weighting));
      b.onclick = () => { if (weighting !== k) { weighting = k; fetchPanel(); } };
      seg.appendChild(b);
    }
    wRow.appendChild(seg);

    if (!res) return;
    const applied = res.weighting;                 // what the panel says it did
    const w = res.weights;
    let note;
    if (res.empty) note = "";
    else if (applied === "single_source")
      note = `${shortName(res.source)}: raw numbers, nothing blended, ` +
             `so weighting does not apply`;
    else if (applied === "earned")
      note = `rows, matrix and totals use earned weights` +
             (w ? `, fit of ${fitStamp(w.as_of)} UTC` : "");
    else note = "rows, matrix and totals use equal weights";
    if (note) wRow.appendChild(el("span", "wnote", note));
    // the one-line finding on the closed disclosure
    const nProv = (res.active_sources || res.sources || []).length;
    const scored = (res.provider_accuracy?.scored_gws || []).length;
    const inside = `earned weights and measured accuracy ` +
      `(${scored} GW${scored === 1 ? "" : "s"} scored) inside`;
    wSum.textContent = res.empty ? "Consensus weighting and measured accuracy"
      : applied === "single_source"
        ? `${shortName(res.source)} raw, nothing blended; ${inside}`
        : `Consensus uses ${applied} weights across ${nProv} ` +
          `provider${nProv === 1 ? "" : "s"}; ${inside}`;

    if (weighting === "earned" && applied !== "single_source") {
      if (w) renderWeightsTable(w);
      else wBox.appendChild(el("p", "sub",
        "no fit yet: the calibration loop has not scored a settled gameweek, " +
        "so there are no earned weights to apply"));
    }
    renderAccuracyStrip(res.provider_accuracy);
    for (const n of res.notes || []) wBox.appendChild(el("p", "sub", n));
  }

  // why each provider carries the weight it does: the fit beside its evidence
  function renderWeightsTable(w) {
    const box = el("div");
    box.appendChild(el("h3", null, "Why these weights"));
    // the equal share is 1/n over the providers that actually publish xPts
    // (n_obs > 0); a p(appear)-only feed is in the fit table but never in
    // the equal-weight mean, so it must not dilute the comparison
    const n = w.rows.filter(r => r.n_obs > 0).length || w.rows.length;
    const share = 1 / Math.max(1, n);
    box.appendChild(el("p", "sub",
      `weight = 1 / pooled squared error, normalised to sum 1, fitted on ` +
      `${w.scored_gws.length} scored gameweek${w.scored_gws.length === 1 ? "" : "s"}` +
      (w.scored_gws.length ? ` (GW${w.scored_gws.join(", GW")})` : "") +
      `. An equal share across the ${n} measured providers is ${fmt2(share)}; ` +
      `"vs equal" is each weight minus that. A provider whose MAE sits above ` +
      `the equal-weight consensus earns less than an equal share; one under ` +
      `the ${w.n_floor}-observation floor is unmeasured and carries no weight.`));
    const wrap = el("div", "scroll-x");
    const t = el("table", "data acc");
    const hr = el("tr");
    for (const [l, num] of [["provider", 0], ["weight", 1], ["vs equal", 1],
                            ["MAE", 1], ["vs consensus", 1],
                            ["player-GWs", 1], ["", 0]])
      hr.appendChild(el("th", num ? "num" : "", l));
    const th_ = el("thead"); th_.appendChild(hr); t.appendChild(th_);
    const tb = el("tbody");
    for (const r of w.rows) {
      const tr = el("tr");
      tr.appendChild(el("td", null, shortName(r.provider)));
      const wt = el("td", "num", fmt2(r.weight));
      if (r.weight > 0) wt.style.fontWeight = "700";
      tr.appendChild(wt);
      const dEq = r.weight - share;
      const eq = el("td", "num", (dEq >= 0 ? "+" : "") + fmt2(dEq));
      eq.title = `${fmt2(r.weight)} against an equal share of ${fmt2(share)}`;
      eq.style.color = dEq >= 0 ? "var(--good)" : "var(--bad)";
      tr.appendChild(eq);
      tr.appendChild(el("td", "num", r.mae == null ? "–" : fmt2(r.mae)));
      const vs = el("td", "num");
      const chip = deltaChip(r.mae, r.baseline_mae);
      vs.textContent = chip ? "" : "–";
      if (chip) vs.appendChild(chip);
      tr.appendChild(vs);
      const nobs = el("td", "num" + (r.n_obs < w.n_floor ? " underfloor" : ""),
                      r.n_obs.toLocaleString());
      if (r.n_obs < w.n_floor)
        nobs.title = `under the ${w.n_floor} floor: shown, never ranked`;
      tr.appendChild(nobs);
      const st = el("td");
      st.appendChild(el("span", r.earned ? "chip good" : "chip",
                        r.earned ? "earned" : "unmeasured"));
      if (!r.earned && r.holdout) st.title = r.holdout;
      tr.appendChild(st);
      tb.appendChild(tr);
    }
    t.appendChild(tb); wrap.appendChild(t); box.append(wrap);
    wBox.appendChild(box);
  }

  // the compact strip: MAE per provider per scored gameweek, always shown
  function renderAccuracyStrip(acc) {
    const box = el("div");
    box.appendChild(el("h3", null, "Provider accuracy"));
    const gws = acc?.scored_gws || [];
    if (!gws.length || !(acc?.rows || []).length) {
      box.appendChild(el("p", "sub",
        "no gameweek scored yet: providers are scored against settled " +
        "actuals by the nightly post-gameweek job"));
      wBox.appendChild(box); return;
    }
    const byProv = new Map();
    for (const r of acc.rows) {
      if (!byProv.has(r.provider)) byProv.set(r.provider, new Map());
      byProv.get(r.provider).set(r.gw, r);
    }
    // alphabetical on purpose: a table sorted by MAE is a ranking, and a
    // cell under the n floor must never take part in one
    const provs = [...byProv.keys()].sort();
    const wrap = el("div", "scroll-x");
    const t = el("table", "data acc");
    const hr = el("tr");
    hr.appendChild(el("th", null, "provider"));
    for (const g of gws) hr.appendChild(el("th", "num", `GW${g}`));
    hr.appendChild(el("th", "num", "mean"));
    const th_ = el("thead"); th_.appendChild(hr); t.appendChild(th_);
    const tb = el("tbody");
    for (const p of provs) {
      const tr = el("tr");
      tr.appendChild(el("td", null, shortName(p)));
      const cells = byProv.get(p);
      let sum = 0, bsum = 0, k = 0;
      for (const g of gws) {
        const c = cells.get(g);
        const td = el("td", "num");
        if (!c) { td.textContent = "–"; tr.appendChild(td); continue; }
        td.appendChild(document.createTextNode(fmt2(c.mae)));
        const chip = deltaChip(c.mae, c.baseline_mae);
        if (chip) td.appendChild(chip);
        if (!c.meets_floor) {
          td.classList.add("underfloor");
          td.title = `${c.n_obs} player-GWs, under the ${acc.n_floor} floor: ` +
                     `shown, never ranked`;
        } else td.title = `${c.n_obs.toLocaleString()} player-GWs`;
        tr.appendChild(td);
        if (c.baseline_mae != null) { sum += c.mae; bsum += c.baseline_mae; k++; }
      }
      const mean = el("td", "num");
      if (k) {
        mean.appendChild(document.createTextNode(fmt2(sum / k)));
        const chip = deltaChip(sum / k, bsum / k);
        if (chip) mean.appendChild(chip);
      } else mean.textContent = "–";
      tr.appendChild(mean);
      tb.appendChild(tr);
    }
    t.appendChild(tb); wrap.appendChild(t); box.appendChild(wrap);
    box.appendChild(el("p", "sub",
      `${gws.length} gameweek${gws.length === 1 ? "" : "s"} scored ` +
      `(GW${gws.join(", GW")}) · MAE = average points missed per player, ` +
      `all players · the small number is the gap to the equal-weight ` +
      `consensus on the same players: green beats it, red trails it · ` +
      `italic cells sit under the ${acc.n_floor} player-GW floor and are ` +
      `shown, never ranked`));
    wBox.appendChild(box);
  }

  // ---- row 2: gameweek chips (toggle any subset) ----
  function renderGws() {
    gwRow.textContent = "";
    gwRow.appendChild(el("span", "tlabel", "Gameweeks"));
    for (const c of res?.gw_coverage || []) {
      const on = gwSel.has(c.gw);
      const chip = el("button", "chip gw" + (on ? " on" : ""), `GW${c.gw}`);
      chip.title = `${c.n_sources} source${c.n_sources !== 1 ? "s" : ""}, ` +
                   `${c.n_players} players — click to show/hide this column`;
      chip.onclick = () => {
        on ? gwSel.delete(c.gw) : gwSel.add(c.gw);
        if (!gwSel.size) gwSel.add(c.gw);      // never zero columns
        fetchPanel();
      };
      gwRow.appendChild(chip);
    }
  }

  // ---- row 3: filters ----
  function renderFilters() {
    filterRow.textContent = "";
    filterRow.appendChild(el("span", "tlabel", "Filter"));

    const seg = el("span", "seg");
    for (const v of ["", "GKP", "DEF", "MID", "FWD"]) {
      const b = el("button", v === pos ? "on" : "", v || "All");
      b.onclick = () => { pos = v; fetchPanel(); };
      seg.appendChild(b);
    }
    filterRow.appendChild(seg);

    const teams = [...new Set((res?.rows || []).map(r => r.team).filter(Boolean))].sort();
    const teamSel = el("select");
    teamSel.appendChild(Object.assign(el("option", null, "all teams"), { value: "" }));
    for (const t of teams)
      teamSel.appendChild(Object.assign(el("option", null, t), { value: t }));
    teamSel.value = team;
    teamSel.onchange = () => { team = teamSel.value; fetchPanel(); };

    const searchIn = el("input");
    searchIn.type = "search"; searchIn.placeholder = "player…"; searchIn.size = 12;
    searchIn.value = search;
    searchIn.oninput = () => { search = searchIn.value; renderBody(); };

    const priceIn = el("input");
    priceIn.type = "number"; priceIn.step = "0.5"; priceIn.placeholder = "max £";
    priceIn.style.width = "72px"; priceIn.value = maxPrice;
    priceIn.onchange = () => { maxPrice = priceIn.value; fetchPanel(); };

    const pappIn = el("input");
    pappIn.type = "number"; pappIn.step = "0.1"; pappIn.min = "0"; pappIn.max = "1";
    pappIn.placeholder = "min p(app)"; pappIn.style.width = "88px"; pappIn.value = minPapp;
    pappIn.onchange = () => { minPapp = pappIn.value; fetchPanel(); };

    const mine = el("label", "chk");
    const cb = el("input"); cb.type = "checkbox"; cb.checked = squadOnly;
    cb.onchange = () => { squadOnly = cb.checked; renderBody(); };
    mine.append(cb, " my squad");

    filterRow.append(teamSel, searchIn, priceIn, pappIn, mine);
  }

  // ---- the matrix ----
  function sortVal(r, cell) {
    if (sortBy.kind === "gw") return cell(r.code, sortBy.gw) ?? -1e9;
    if (sortBy.kind === "sum") return r._sum;
    const v = r[sortBy.key];
    return v == null ? -1e9 : (typeof v === "string" ? v : v);
  }
  function th(label, sortSpec, opts = {}) {
    const cls = (opts.num !== false ? "num" : "") +
      (sameSort(sortSpec) ? " sorted" : "");
    const h = el("th", cls, label);
    if (sameSort(sortSpec)) h.dataset.dir = sortDir === -1 ? "▼" : "▲";
    if (opts.title) h.title = opts.title;
    h.onclick = () => {
      sortDir = sameSort(sortSpec) ? -sortDir : -1;
      sortBy = sortSpec;
      renderBody();
    };
    return h;
  }
  const sameSort = spec =>
    JSON.stringify(spec) === JSON.stringify(sortBy);

  function renderAccuracy(host) {
    const all = (res?.accuracy || []);
    const accs = all.filter(a => a.scope === accScope && (a.mae != null || a.n_obs));
    if (!all.length) return;
    const box = el("div");
    const hd = el("div", "toolbar");
    hd.appendChild(el("h2", null, "Measured accuracy"));
    const seg = el("span", "seg");
    for (const [k, label] of [["overall", "all players"],
                              ["own_gt5", ">5% owned"],
                              ["own_gt20", ">20% owned"]]) {
      const b = el("button", k === accScope ? "on" : "", label);
      b.onclick = () => { accScope = k; renderBody(); };
      seg.appendChild(b);
    }
    hd.appendChild(seg);
    box.appendChild(hd);
    const gwN = all.find(a => a.track_record_gws)?.track_record_gws ?? "?";
    const scopeNote = accScope === "overall"
      ? "the whole board — a model must price everyone"
      : accScope === "own_gt5"
        ? "players over 5% owned — where transfer decisions actually live"
        : "the template (over 20% owned) — tiny sample, read gently";
    box.appendChild(el("p", "sub",
      `Last pre-deadline projections scored against settled actuals · ` +
      `${gwN} gameweek(s) of track record · ${scopeNote}. ` +
      `MAE = average miss per player; RMSE punishes big misses harder. ` +
      `Baseline = predicting the all-provider mean.`));
    if (!accs.length) {
      box.appendChild(el("p", "sub", "no rows for this cohort yet"));
      host.appendChild(box); return;
    }
    accs.sort((a, b) => (a.mae ?? 99) - (b.mae ?? 99));
    const wrap = el("div", "scroll-x");
    const t = el("table", "data");
    const hr2 = el("tr");
    for (const [l, num] of [["provider", 0], ["MAE", 1], ["vs base", 1],
                            ["RMSE", 1], ["vs base", 1],
                            ["players", 1], ["weight", 1], ["", 0]])
      hr2.appendChild(el("th", num ? "num" : "", l));
    const th_ = el("thead"); th_.appendChild(hr2); t.appendChild(th_);
    const tb = el("tbody");
    const deltaTd = (v, base) => {
      const d = (v != null && base != null) ? base - v : null;
      const td = el("td", "num",
        d == null ? "–" : (d >= 0 ? "+" : "") + fmt2(d));
      if (d != null) td.style.color = d >= 0 ? "var(--good)" : "var(--bad)";
      return td;
    };
    for (const a of accs) {
      const tr = el("tr");
      tr.appendChild(el("td", null, a.provider.replace(/^gh_/, "")));
      tr.appendChild(el("td", "num", a.mae == null ? "–" : fmt2(a.mae)));
      tr.appendChild(deltaTd(a.mae, a.baseline_mae));
      tr.appendChild(el("td", "num", a.rmse == null ? "–" : fmt2(a.rmse)));
      tr.appendChild(deltaTd(a.rmse, a.baseline_rmse));
      tr.appendChild(el("td", "num", String(a.n_obs)));
      tr.appendChild(el("td", "num", fmt2(a.weight)));
      const st = el("td");
      st.appendChild(el("span", a.earned ? "chip good" : "chip",
                        a.earned ? "earned" : "unmeasured"));
      tr.appendChild(st);
      tb.appendChild(tr);
    }
    t.appendChild(tb); wrap.appendChild(t); box.appendChild(wrap);
    host.appendChild(box);
  }

  function renderBody() {
    body.textContent = "";
    if (showAccuracy) renderAccuracy(body);
    const gws = [...gwSel].sort((a, b) => a - b).filter(g => (res.gws || []).includes(g));
    const mx = res.matrix || {};
    const cell = (code, g) => mx[String(code)]?.[String(g)] ?? null;

    let rows = (res.rows || []).map(r => ({
      ...r, _sum: gws.reduce((a, g) => a + (cell(r.code, g) ?? 0), 0),
    }));
    const term = search.trim().toLowerCase();
    if (term) rows = rows.filter(r => r.name.toLowerCase().includes(term));
    if (squadOnly) rows = rows.filter(r => squadCodes.has(r.code));
    rows.sort((a, b) => {
      const x = sortVal(a, cell), y = sortVal(b, cell);
      if (typeof x === "string" || typeof y === "string")
        return String(x).localeCompare(String(y)) * sortDir;
      return (y - x) * -sortDir;
    });

    let tintMax = 0.001;
    for (const r of rows) for (const g of gws) {
      const v = cell(r.code, g); if (v > tintMax) tintMax = v;
    }

    const wrap = el("div", "scroll-x");
    const table = el("table", "data sticky-first matrix");
    const thead = el("thead"); const hr = el("tr");
    hr.appendChild(th("player", { kind: "col", key: "name" }, { num: false }));
    hr.appendChild(th("pos", { kind: "col", key: "pos" }, { num: false }));
    hr.appendChild(th("team", { kind: "col", key: "team" }, { num: false }));
    hr.appendChild(th("£", { kind: "col", key: "price" }));
    hr.appendChild(th("own%", { kind: "col", key: "own_pct" }));
    for (const g of gws) {
      const settled = (res.settled_gws || []).includes(g);
      hr.appendChild(th(settled ? `GW${g} ✓` : `GW${g}`, { kind: "gw", gw: g },
        { title: settled
            ? `GW${g} is settled: cells show ACTUAL points with the delta vs projection`
            : `sort by GW${g}` }));
    }
    hr.appendChild(th("Σ", { kind: "sum" },
      { title: "total over the shown gameweeks" }));
    hr.appendChild(th("±", { kind: "col", key: "spread" },
      { title: `cross-source disagreement at GW${res.gw}` }));
    hr.appendChild(th("p(app)", { kind: "col", key: "p_appear" },
      { title: "probability of appearing — its own column, never multiplied into xPts" }));
    thead.appendChild(hr); table.appendChild(thead);
    const tbody = el("tbody");

    for (const r of rows.slice(0, 100)) {
      const tr = el("tr");
      const nameTd = el("td");
      const face = faceImg(r.code, "avatar" +
        (squadCodes.has(r.code) ? " mine" : ""));
      if (squadCodes.has(r.code)) face.title = "in your squad";
      nameTd.appendChild(face);
      nameTd.appendChild(document.createTextNode(r.name));
      if (r.status && r.status !== "a")
        nameTd.appendChild(el("span", "chip warn", ` ${r.status}`));
      nameTd.style.cursor = "pointer";
      nameTd.title = "click for the per-source breakdown";
      nameTd.onclick = () => showDetail(r);
      tr.appendChild(nameTd);
      tr.appendChild(el("td", null, r.pos));
      tr.appendChild(el("td", null, r.team ?? "–"));
      tr.appendChild(el("td", "num", fmtPrice(r.price)));
      tr.appendChild(el("td", "num", r.own_pct == null ? "–" : fmt1(r.own_pct)));
      for (const g of gws) {
        const v = cell(r.code, g);
        const settled = (res.settled_gws || []).includes(g);
        const act = settled ? res.actuals?.[String(r.code)]?.[String(g)] : null;
        const td = el("td", "num");
        if (settled) {
          // History: the actual leads; the delta vs projection judges the call
          if (act == null && v == null) td.textContent = "–";
          else {
            const a = act ?? 0;
            td.appendChild(el("b", null, String(Math.round(a))));
            if (v != null) {
              const d = a - v;
              const chip = el("span", "delta " + (d >= 0 ? "over" : "under"),
                ` ${d >= 0 ? "+" : ""}${fmt1(d)}`);
              chip.title = `projected ${fmt1(v)}, actual ${Math.round(a)}`;
              td.appendChild(chip);
            }
            td.style.background =
              `color-mix(in oklab, var(${v != null && a - v >= 0 ? "--good" : "--bad"}) ` +
              `${v == null ? 0 : Math.min(22, Math.round(Math.abs(a - v) * 6))}%, var(--surface))`;
          }
        } else if (v == null) td.textContent = "–";
        else {
          const pct = Math.min(58, Math.round(58 * v / tintMax));
          td.textContent = fmt1(v);
          td.style.background = `color-mix(in oklab, var(--s1) ${pct}%, var(--surface))`;
          if (pct > 48) td.style.color = "#fff";
        }
        tr.appendChild(td);
      }
      tr.appendChild(el("td", "num sum", fmt1(r._sum)));
      tr.appendChild(el("td", "num", r.spread == null ? "–" : fmt1(r.spread)));
      tr.appendChild(el("td", "num", r.p_appear == null ? "–" : fmt2(r.p_appear)));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody); wrap.appendChild(table);
    body.appendChild(wrap);
    const priceAge = res.prices_as_of
      ? `prices as of ${fmtSpan((Date.now() -
          new Date(res.prices_as_of.replace(" ", "T"))) / 3.6e6)} ago`
      : "price age unknown";
    const blend = res.weighting === "single_source"
      ? `${shortName(res.source)} raw, no blend`
      : `consensus: ${res.weighting} weights`;
    body.appendChild(el("p", "sub",
      `${rows.length} players · showing ${Math.min(100, rows.length)} · ` +
      `${blend} · ` +
      `ring on a photo = in your squad · click any header to sort · ` +
      `tint = xPts magnitude · ✓ columns are settled: bold actual, ` +
      `green over / red under projection · ` +
      `${priceAge} (refreshed nightly and T-30h)`));
  }

  // ---- per-source breakdown: the SHARED drawer ----
  function showDetail(r) {
    showPlayerDetail(dh, r, {
      gw: res.gws?.[0] ?? res.gw,
      actuals: res.actuals?.[String(r.code)] || {},
    });
  }

  await fetchPanel();
}
