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
  const detailBox = el("div");
  const foot = el("div");
  card.append(srcRow, gwRow, filterRow, body, detailBox, foot);
  host.appendChild(card);

  // ---- state ----
  let picked = new Set();          // sources; empty = all (full consensus)
  let gwSel = new Set();           // chosen gameweeks; filled after first load
  let pos = "", team = "", search = "", maxPrice = "", minPapp = "";
  let squadOnly = false;
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
    for (const m of metas) {
      const on = picked.has(m.source);
      const a = ageInfo(m.last_fetched);
      const chip = el("button", "chip src" + (on ? " on" : ""));
      chip.appendChild(document.createTextNode((on ? "✓ " : "")));
      chip.appendChild(el("span", "freshdot " + a.cls));
      chip.appendChild(document.createTextNode(
        ` ${m.source.replace(/^gh_/, "")} · ${a.text}`));
      chip.title = `GW${m.gw_min}–${m.gw_max} · ${m.n_rows.toLocaleString()} rows` +
        (m.has_p_appear ? " · p(appear)" : "") + (m.has_xmins ? " · xMins" : "") +
        `\nlast fetch ${m.last_fetched}\nclick to include/exclude`;
      chip.onclick = () => {
        picked.has(m.source) ? picked.delete(m.source) : picked.add(m.source);
        fetchPanel();
      };
      srcRow.appendChild(chip);
    }
    const n = (res?.active_sources || res?.sources || []).length;
    srcRow.appendChild(el("span", "sub",
      picked.size ? `consensus of ${n} selected` : `consensus of all ${n}`));
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

  function renderBody() {
    body.textContent = "";
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
    for (const g of gws)
      hr.appendChild(th(`GW${g}`, { kind: "gw", gw: g },
        { title: `sort by GW${g}` }));
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
      if (squadCodes.has(r.code)) {
        const dot = el("span", "minedot");
        dot.title = "in your squad";
        nameTd.appendChild(dot);
      }
      nameTd.appendChild(faceImg(r.code, "avatar"));
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
        const td = el("td", "num");
        if (v == null) td.textContent = "–";
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
    body.appendChild(el("p", "sub",
      `${rows.length} players · showing ${Math.min(100, rows.length)} · ` +
      `● before a name = in your squad · click any header to sort · ` +
      `tint = xPts magnitude · p(appear) is never folded into xPts`));
  }

  // ---- per-source breakdown ----
  async function showDetail(r) {
    detailBox.textContent = "";
    const box = el("div", "card");
    box.appendChild(el("h2", null, `${r.name} — every source`));
    box.appendChild(el("p", "sub", "loading…"));
    detailBox.appendChild(box);
    try {
      const { result } = await runPanel("projection_table",
        { gw: res.gw, detail_code: r.code, limit: 1 });
      box.textContent = "";
      const head = el("div", "toolbar");
      head.appendChild(el("h2", null, `${r.name} — every source`));
      const close = el("button", null, "close");
      close.onclick = () => { detailBox.textContent = ""; };
      head.appendChild(close);
      box.appendChild(head);
      const d = result.detail;
      const rowsD = d && (d.rows || d.sources);
      if (!rowsD || !rowsD.length) {
        box.appendChild(emptyBox("no per-source rows for this player"));
        return;
      }
      const wrap = el("div", "scroll-x");
      const table = el("table", "data");
      const thead = el("thead"); const hr2 = el("tr");
      const cols = Object.keys(rowsD[0]);
      for (const c of cols) hr2.appendChild(el("th", null, c));
      thead.appendChild(hr2); table.appendChild(thead);
      const tbody = el("tbody");
      for (const row of rowsD) {
        const tr = el("tr");
        for (const c of cols)
          tr.appendChild(el("td", typeof row[c] === "number" ? "num" : "",
            row[c] == null ? "–" : String(row[c])));
        tbody.appendChild(tr);
      }
      table.appendChild(tbody); wrap.appendChild(table);
      box.appendChild(wrap);
      if (d.outlier) box.appendChild(el("p", "sub", `outlier: ${d.outlier}`));
    } catch (e) { box.textContent = ""; box.appendChild(errBox(e)); }
  }

  await fetchPanel();
}
