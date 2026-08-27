/* xPoints — the projection matrix (fplreview idiom).
   Rows = players, columns = gameweeks, cells = xPts tinted by magnitude with
   the number always printed. One selection drives everything: consensus
   across every ingested source, or any single source. The sources strip
   states exactly where numbers come from and how fresh each feed is — a
   newly ingested provider (a paid FPL Review feed, say) appears there with
   no UI change, because the strip renders whatever source_meta declares. */

import { runPanel, el, emptyBox, errBox, provenance, faceImg,
         fmtPrice, fmt1, fmt2 } from "/js/app.js";

const POS_NAMES = { 1: "GKP", 2: "DEF", 3: "MID", 4: "FWD" };
const SORTS = {
  window: { label: "Σ window",   val: r => r._sum },
  xpts:   { label: "anchor xPts", val: r => r.xpts },
  spread: { label: "disagreement", val: r => r.spread ?? -1 },
  value:  { label: "value (Σ/£)", val: r => r._sum / (r.price || 1) },
  p_appear: { label: "p(appear)", val: r => r.p_appear ?? -1 },
  own:    { label: "owned %",     val: r => r.own_pct ?? -1 },
  price:  { label: "price",       val: r => r.price ?? -1 },
};

export default async function xpoints(host) {
  const card = el("section", "card");
  card.appendChild(el("h2", null, "Projections"));
  card.appendChild(el("p", "sub",
    "Every number below is COPIED from an ingested provider, never modelled " +
    "here. Feeds refresh automatically: T-30h before each deadline and " +
    "nightly after matches; a newly ingested provider appears in the strip " +
    "with no UI change."));
  const srcStrip = el("div");
  const controls = el("div");
  const body = el("div");
  const detailBox = el("div");
  const foot = el("div");
  card.append(srcStrip, controls, body, detailBox, foot);
  host.appendChild(card);

  // ---- state ----
  let anchor = "next", span = 5, source = "";   // "" = consensus
  let pos = "", team = "", search = "", maxPrice = "", minPapp = "";
  let squadOnly = false, sortKey = "window", sortDir = -1;
  let res = null, squadCodes = new Set();

  // my squad, for row highlighting — parallel, non-blocking, optional
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
    const params = { gw: anchor === "next" ? "next" : Number(anchor),
                     span: Number(span), limit: 200 };
    if (source) params.source = source;
    if (pos) params.position = { GKP: 1, DEF: 2, MID: 3, FWD: 4 }[pos];
    if (team) params.team = team;
    if (maxPrice) params.max_price = Number(maxPrice);
    if (minPapp) params.min_p_appear = Number(minPapp);
    try {
      const { result, provenance: prov } = await runPanel("projection_table", params);
      res = result;
      foot.textContent = "";
      foot.appendChild(provenance(prov));
      renderSources();
      renderControls();
      if (res.empty) { body.textContent = ""; body.appendChild(emptyBox(res.reason)); return; }
      renderBody();
    } catch (e) { body.textContent = ""; body.appendChild(errBox(e)); }
  }

  // ---- the sources strip: what and how fresh ----
  function ageInfo(iso) {
    const h = (Date.now() - new Date(iso.replace(" ", "T"))) / 3.6e6;
    if (!isFinite(h)) return { cls: "bad", text: "unknown age" };
    if (h < 36) return { cls: "good", text: h < 1.5 ? "fresh" : `${Math.round(h)}h ago` };
    if (h < 72) return { cls: "warn", text: `${Math.round(h)}h ago` };
    return { cls: "bad", text: `${Math.round(h / 24)}d ago` };
  }
  function renderSources() {
    srcStrip.textContent = "";
    const metas = res?.source_meta || [];
    if (!metas.length) return;
    const row = el("div", "filters");
    row.appendChild(el("label", null, "sources:"));
    for (const m of metas) {
      const a = ageInfo(m.last_fetched);
      const chip = el("span", "chip src" + (source === m.source ? " s1" : ""));
      chip.appendChild(el("span", "freshdot " + a.cls));
      chip.appendChild(document.createTextNode(
        ` ${m.source} · GW${m.gw_min}–${m.gw_max} · ${a.text}`));
      chip.title = `${m.n_rows.toLocaleString()} projection rows` +
        (m.has_p_appear ? " · publishes p(appear)" : "") +
        (m.has_xmins ? " · publishes xMins" : "") +
        `\nlast fetch ${m.last_fetched}\nclick to view only this source`;
      chip.style.cursor = "pointer";
      chip.onclick = () => { source = source === m.source ? "" : m.source; fetchPanel(); };
      row.appendChild(chip);
    }
    const cons = el("span", "chip src" + (source === "" ? " s1" : ""),
                    `consensus (${metas.length} sources)`);
    cons.title = "the mean across every source; the spread column is how much they disagree";
    cons.style.cursor = "pointer";
    cons.onclick = () => { source = ""; fetchPanel(); };
    row.insertBefore(cons, row.children[1]);
    srcStrip.appendChild(row);
  }

  // ---- controls ----
  function renderControls() {
    controls.textContent = "";
    const f1 = el("div", "filters");

    const gwSel = el("select");
    gwSel.appendChild(Object.assign(el("option", null, "next GW"), { value: "next" }));
    for (const c of res?.gw_coverage || [])
      gwSel.appendChild(Object.assign(
        el("option", null, `GW${c.gw} (${c.n_sources} src)`), { value: c.gw }));
    gwSel.value = String(anchor);
    gwSel.onchange = () => { anchor = gwSel.value; fetchPanel(); };

    const spanSel = el("select");
    for (const n of [1, 3, 5, 8])
      spanSel.appendChild(Object.assign(
        el("option", null, `${n} GW${n > 1 ? "s" : ""}`), { value: n }));
    spanSel.value = String(span);
    spanSel.onchange = () => { span = Number(spanSel.value); fetchPanel(); };

    const seg = el("span", "seg");
    for (const v of ["", "GKP", "DEF", "MID", "FWD"]) {
      const b = el("button", v === pos ? "on" : "", v || "ALL");
      b.onclick = () => { pos = v; fetchPanel(); };
      seg.appendChild(b);
    }

    const teams = [...new Set((res?.rows || []).map(r => r.team).filter(Boolean))].sort();
    const teamSel = el("select");
    teamSel.appendChild(Object.assign(el("option", null, "all teams"), { value: "" }));
    for (const t of teams)
      teamSel.appendChild(Object.assign(el("option", null, t), { value: t }));
    teamSel.value = team;
    teamSel.onchange = () => { team = teamSel.value; fetchPanel(); };

    f1.append(el("label", null, "from"), gwSel, spanSel, seg, teamSel);
    controls.appendChild(f1);

    const f2 = el("div", "filters");
    const searchIn = el("input");
    searchIn.type = "text"; searchIn.placeholder = "search player"; searchIn.size = 14;
    searchIn.value = search;
    searchIn.oninput = () => { search = searchIn.value; renderBody(); };

    const priceIn = el("input");
    priceIn.type = "number"; priceIn.step = "0.5"; priceIn.placeholder = "max £";
    priceIn.style.width = "70px"; priceIn.value = maxPrice;
    priceIn.onchange = () => { maxPrice = priceIn.value; fetchPanel(); };

    const pappIn = el("input");
    pappIn.type = "number"; pappIn.step = "0.1"; pappIn.min = "0"; pappIn.max = "1";
    pappIn.placeholder = "min p(app)"; pappIn.style.width = "84px"; pappIn.value = minPapp;
    pappIn.onchange = () => { minPapp = pappIn.value; fetchPanel(); };

    const sortSel = el("select");
    for (const [k, sdef] of Object.entries(SORTS))
      sortSel.appendChild(Object.assign(
        el("option", null, `sort: ${sdef.label}`), { value: k }));
    sortSel.value = sortKey;
    sortSel.onchange = () => { sortKey = sortSel.value; sortDir = -1; renderBody(); };

    const mine = el("label");
    const cb = el("input"); cb.type = "checkbox"; cb.checked = squadOnly;
    cb.onchange = () => { squadOnly = cb.checked; renderBody(); };
    mine.append(cb, " my squad");

    f2.append(searchIn, priceIn, pappIn, sortSel, mine);
    controls.appendChild(f2);
  }

  // ---- the matrix ----
  function renderBody() {
    body.textContent = "";
    const gws = res.gws || [res.gw];
    const mx = res.matrix || {};
    const cell = (code, g) => mx[String(code)]?.[String(g)] ?? null;

    let rows = (res.rows || []).map(r => ({
      ...r,
      _sum: gws.reduce((a, g) => a + (cell(r.code, g) ?? 0), 0),
    }));
    const term = search.trim().toLowerCase();
    if (term) rows = rows.filter(r => r.name.toLowerCase().includes(term));
    if (squadOnly) rows = rows.filter(r => squadCodes.has(r.code));
    const S = SORTS[sortKey];
    rows.sort((a, b) => (S.val(b) - S.val(a)) * -sortDir);

    let tintMax = 0.001;
    for (const r of rows) for (const g of gws) {
      const v = cell(r.code, g); if (v > tintMax) tintMax = v;
    }

    const wrap = el("div", "scroll-x");
    const table = el("table", "data sticky-first");
    const thead = el("thead"); const hr = el("tr");
    const headers = [["player", null], ["pos", null], ["team", null],
                     ["£", "price"], ["own%", "own"]];
    for (const [lbl, key] of headers) {
      const th = el("th", key ? "num" : "", lbl);
      if (key) th.onclick = () => {
        sortDir = sortKey === key ? -sortDir : -1; sortKey = key; renderBody();
      };
      hr.appendChild(th);
    }
    for (const g of gws) {
      const th = el("th", "num", `GW${g}`);
      th.title = "click to sort this gameweek";
      th.onclick = () => { sortKey = "xpts"; renderBody(); };
      hr.appendChild(th);
    }
    for (const [lbl, key] of [["Σ", "window"], ["±", "spread"], ["p(app)", "p_appear"]]) {
      const th = el("th", "num", lbl);
      th.title = key === "spread"
        ? "cross-source disagreement at the anchor GW (max − min)"
        : key === "window" ? "total over the window" : "probability of appearing";
      th.onclick = () => {
        sortDir = sortKey === key ? -sortDir : -1; sortKey = key; renderBody();
      };
      hr.appendChild(th);
    }
    thead.appendChild(hr); table.appendChild(thead);
    const tbody = el("tbody");

    for (const r of rows.slice(0, 100)) {
      const tr = el("tr");
      if (squadCodes.has(r.code)) tr.classList.add("mine");
      const nameTd = el("td");
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
          const pct = Math.min(62, Math.round(62 * v / tintMax));
          td.textContent = fmt1(v);
          td.style.background = `color-mix(in oklab, var(--s1) ${pct}%, var(--surface))`;
          if (pct > 52) td.style.color = "#fff";
        }
        tr.appendChild(td);
      }
      const sumTd = el("td", "num", fmt1(r._sum));
      sumTd.style.fontWeight = "700";
      tr.appendChild(sumTd);
      tr.appendChild(el("td", "num",
        r.spread == null ? "–" : `${fmt1(r.spread)}`));
      tr.appendChild(el("td", "num",
        r.p_appear == null ? "–" : fmt2(r.p_appear)));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody); wrap.appendChild(table);
    body.appendChild(wrap);
    body.appendChild(el("p", "sub",
      `${rows.length} players · showing ${Math.min(100, rows.length)} · ` +
      `cells: ${source || "consensus"} xPts, tint = magnitude · ` +
      `± = cross-source disagreement at GW${res.gw} · ` +
      `p(appear) is its own column, never multiplied into xPts`));
  }

  // ---- per-source breakdown ----
  async function showDetail(r) {
    detailBox.textContent = "";
    const box = el("div", "card");
    box.appendChild(el("h2", null, `${r.name} — every source, GW${res.gw}+`));
    box.appendChild(el("p", "sub", "loading…"));
    detailBox.appendChild(box);
    try {
      const params = { gw: res.gw, detail_code: r.code, limit: 1 };
      if (source) params.source = source;
      const { result } = await runPanel("projection_table", params);
      box.textContent = "";
      const head = el("div", "filters");
      head.appendChild(el("h2", null, `${r.name} — every source`));
      const close = el("button", null, "close");
      close.onclick = () => { detailBox.textContent = ""; };
      head.appendChild(close);
      box.appendChild(head);
      const d = result.detail;
      if (!d || !(d.rows || d.sources || []).length) {
        box.appendChild(emptyBox("no per-source rows for this player"));
        return;
      }
      const rowsD = d.rows || d.sources;
      const wrap = el("div", "scroll-x");
      const table = el("table", "data");
      const thead = el("thead"); const hr = el("tr");
      const cols = Object.keys(rowsD[0]);
      for (const c of cols) hr.appendChild(el("th", null, c));
      thead.appendChild(hr); table.appendChild(thead);
      const tbody = el("tbody");
      for (const row of rowsD) {
        const tr = el("tr");
        for (const c of cols) {
          const v = row[c];
          tr.appendChild(el("td",
            typeof v === "number" ? "num" : "",
            v == null ? "–" : String(v)));
        }
        tbody.appendChild(tr);
      }
      table.appendChild(tbody); wrap.appendChild(table);
      box.appendChild(wrap);
      if (d.outlier) box.appendChild(el("p", "sub", `outlier: ${d.outlier}`));
    } catch (e) { box.textContent = ""; box.appendChild(errBox(e)); }
  }

  await fetchPanel();
}
