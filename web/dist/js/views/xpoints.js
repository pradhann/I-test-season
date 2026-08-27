/* xPoints — the projection matrix (fplreview idiom, designed to be operated).
   One mental model: pick WHOSE numbers (source chips, multi-select), pick
   WHICH gameweeks (toggleable GW chips), then read a matrix where every
   column header sorts. Squad membership is a quiet dot, not a shout. */

import { runPanel, el, emptyBox, errBox, provenance, faceImg,
         fmtPrice, fmt1, fmt2 } from "/js/app.js";

export default async function xpoints(host) {
  const card = el("section", "card");
  card.appendChild(el("h2", null, "Projections"));
  card.appendChild(el("p", "sub",
    "Numbers are copied from ingested providers, never modelled here. Feeds " +
    "refresh T-30h before each deadline and nightly; a newly ingested " +
    "provider appears below automatically."));
  const srcRow = el("div", "toolbar");
  const gwRow = el("div", "toolbar");
  const filterRow = el("div", "toolbar");
  const body = el("div");
  const foot = el("div");
  const drawer = el("aside", "drawer");
  document.body.appendChild(drawer);
  addEventListener("keydown", e => {
    if (e.key === "Escape") drawer.classList.remove("open");
  });
  card.append(srcRow, gwRow, filterRow, body, foot);
  host.appendChild(card);

  // ---- state ----
  let picked = new Set();          // sources; empty = all (full consensus)
  let gwSel = new Set();           // chosen gameweeks; filled after first load
  let pos = "", team = "", search = "", maxPrice = "", minPapp = "";
  let squadOnly = false, showAccuracy = false, accScope = "own_gt5";
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
    const params = { gw: anchor, span, limit: 200 };
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
        renderSources(); body.textContent = "";
        body.appendChild(emptyBox(res.reason)); return;
      }
      if (!gwSel.size)
        gwSel = new Set((res.gws || []).slice(0, 5));
      renderSources(); renderGws(); renderFilters(); renderBody();
    } catch (e) { body.textContent = ""; body.appendChild(errBox(e)); }
  }

  // ---- row 1: sources (multi-select chips with freshness) ----
  function ageInfo(iso) {
    const h = (Date.now() - new Date(iso.replace(" ", "T"))) / 3.6e6;
    if (!isFinite(h)) return { cls: "bad", text: "?" };
    if (h < 36) return { cls: "good", text: h < 1.5 ? "fresh" : `${Math.round(h)}h` };
    if (h < 72) return { cls: "warn", text: `${Math.round(h)}h` };
    return { cls: "bad", text: `${Math.round(h / 24)}d` };
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
      ? `prices as of ${Math.round((Date.now() -
          new Date(res.prices_as_of.replace(" ", "T"))) / 3.6e6)}h ago`
      : "price age unknown";
    body.appendChild(el("p", "sub",
      `${rows.length} players · showing ${Math.min(100, rows.length)} · ` +
      `ring on a photo = in your squad · click any header to sort · ` +
      `tint = xPts magnitude · ✓ columns are settled: bold actual, ` +
      `green over / red under projection · ` +
      `${priceAge} (refreshed nightly and T-30h)`));
  }

  // ---- per-source breakdown ----
  async function showDetail(r) {
    drawer.textContent = "";
    drawer.classList.add("open");
    drawer.appendChild(el("p", "sub", "loading…"));
    try {
      const { result } = await runPanel("projection_table",
        { gw: res.gws?.[0] ?? res.gw, span: 8, detail_code: r.code, limit: 1 });
      drawer.textContent = "";

      // header: who this is
      const head = el("div", "dhead");
      head.appendChild(faceImg(r.code, "bigface"));
      const id = el("div");
      id.appendChild(el("div", "dname", r.name));
      id.appendChild(el("div", "sub",
        [r.pos, r.team, fmtPrice(r.price),
         r.own_pct != null ? fmt1(r.own_pct) + "% owned" : null]
          .filter(Boolean).join(" · ")));
      head.appendChild(id);
      const close = el("button", null, "✕");
      close.onclick = () => drawer.classList.remove("open");
      head.appendChild(close);
      drawer.appendChild(head);

      // settled reality first, if any
      const acts = res.actuals?.[String(r.code)] || {};
      const settled = Object.keys(acts).sort();
      if (settled.length)
        drawer.appendChild(el("p", "sub", "Settled: " + settled.map(g =>
          `GW${g} → ${Math.round(acts[g])} pts`).join(" · ")));

      // pivot: sources × gameweeks
      const rowsD = (result.detail?.rows) || [];
      if (!rowsD.length) {
        drawer.appendChild(emptyBox("no per-source projections for this player"));
        return;
      }
      const srcs = [...new Set(rowsD.map(x => x.source))];
      const dgws = [...new Set(rowsD.map(x => x.gw))].sort((a, b) => a - b);
      const at = (src, g) =>
        rowsD.find(x => x.source === src && x.gw === g) || {};
      let dmax = 0.001;
      for (const x of rowsD) if (x.xpts > dmax) dmax = x.xpts;

      drawer.appendChild(el("h2", null, "Projected points by source"));
      const wrap = el("div", "scroll-x");
      const t = el("table", "data");
      const hd = el("tr");
      hd.appendChild(el("th", null, "source"));
      for (const g of dgws) hd.appendChild(el("th", "num", `GW${g}`));
      const th_ = el("thead"); th_.appendChild(hd); t.appendChild(th_);
      const tb = el("tbody");
      for (const src of srcs) {
        const tr = el("tr");
        tr.appendChild(el("td", null, src.replace(/^gh_/, "")));
        for (const g of dgws) {
          const v = at(src, g).xpts;
          const td = el("td", "num");
          if (v == null) td.textContent = "–";
          else {
            td.textContent = fmt1(v);
            const pct = Math.min(45, Math.round(45 * v / dmax));
            td.style.background =
              `color-mix(in oklab, var(--s1) ${pct}%, var(--surface))`;
          }
          tr.appendChild(td);
        }
        tb.appendChild(tr);
      }
      // consensus row, bold
      const cr = el("tr");
      cr.appendChild(el("td", null, "consensus"));
      cr.style.fontWeight = "700";
      for (const g of dgws) {
        const vs = srcs.map(sc => at(sc, g).xpts).filter(v => v != null);
        cr.appendChild(el("td", "num",
          vs.length ? fmt1(vs.reduce((a, b) => a + b, 0) / vs.length) : "–"));
      }
      tb.appendChild(cr);
      t.appendChild(tb); wrap.appendChild(t); drawer.appendChild(wrap);

      // appearance odds, where any source publishes them
      const pa = rowsD.filter(x => x.p_appear != null);
      if (pa.length) {
        const first = pa[0];
        drawer.appendChild(el("p", "sub",
          `p(appear) ${fmt2(first.p_appear)}` +
          (first.xp_if_appears != null
            ? ` · ${fmt1(first.xp_if_appears)} xPts if he plays` : "") +
          ` (${pa[0].source.replace(/^gh_/, "")})`));
      }
      drawer.appendChild(el("p", "sub",
        "Row tint = that source's own scale. The gap between rows IS the " +
        "uncertainty."));
    } catch (e) { drawer.textContent = ""; drawer.appendChild(errBox(e)); }
  }

  await fetchPanel();
}
