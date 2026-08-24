/* xPoints: the projection board over every ingested source.
   Consensus mode shows the cross-source mean with the min–max SPREAD as a
   first-class sortable column — source disagreement IS the uncertainty.
   Picking one source shows that vendor raw; clicking a row opens a
   per-source breakdown across the next five gameweeks with the outlier
   flagged. p(appear) stays a separate column from xPts, never multiplied
   in (FPLForm's deliberate design — the rank layer needs them separate). */

import { runPanel, el, emptyBox, errBox, provenance, dataTable, bar,
         fmtPrice, fmt1, fmt2 } from "/js/app.js";

const POSITIONS = [["", "all positions"], ["1", "GKP"], ["2", "DEF"],
                   ["3", "MID"], ["4", "FWD"]];

export default async function view(host) {
  const state = {
    gw: "next", source: "all", position: null, team: null,
    max_price: null, min_p_appear: null, sort: "xpts",
    detail_code: null, limit: 150,
  };
  // Survives an empty response so the GW picker still offers a way out.
  const known = { coverage: [], sources: [], teams: [] };

  const card = el("section", "card");
  card.appendChild(el("h2", null, "xPoints — source consensus"));
  card.appendChild(el("p", "sub",
    "Per-player consensus across every ingested projection source. " +
    "Spread = max−min across sources. p(appear) is its own column and is " +
    "never folded into xPts. Click a row for the per-source breakdown."));
  const filtersHost = el("div");
  const aggHost = el("div");
  const tableHost = el("div");
  const detailHost = el("div");
  const provHost = el("div");
  card.append(filtersHost, aggHost, tableHost, detailHost, provHost);
  host.appendChild(card);

  function params() {
    const p = { gw: state.gw, sort: state.sort, limit: state.limit };
    if (state.source && state.source !== "all") p.source = state.source;
    if (state.position) p.position = Number(state.position);
    if (state.team) p.team = state.team;
    if (state.max_price != null) p.max_price = state.max_price;
    if (state.min_p_appear != null) p.min_p_appear = state.min_p_appear;
    if (state.detail_code != null) p.detail_code = state.detail_code;
    return p;
  }

  async function load() {
    tableHost.textContent = "loading…";
    aggHost.textContent = ""; detailHost.textContent = "";
    provHost.textContent = "";
    try {
      const { result, provenance: prov } = await runPanel("projection_table", params());
      if (result.empty) {
        renderFilters();               // last known options: pick another GW
        tableHost.textContent = "";
        tableHost.appendChild(emptyBox(result.reason,
          "Filters and the gameweek picker above still work."));
      } else {
        state.gw = result.gw;          // "next" resolved server-side
        known.coverage = result.gw_coverage || [];
        known.sources = result.sources || [];
        known.teams = (result.by_team || []).map(t => t.team).sort();
        renderFilters();
        renderAggregates(result);
        renderTable(result);
        renderDetail(result.detail);
        for (const n of result.notes || []) {
          provHost.appendChild(el("p", "sub", n));
        }
      }
      provHost.appendChild(provenance(prov));
    } catch (e) {
      tableHost.textContent = "";
      tableHost.appendChild(errBox(e));
    }
  }

  // ---- filters: one row, the dataviz filter idiom -------------------------
  function renderFilters() {
    filtersHost.textContent = "";
    const row = el("div", "filters");

    const sel = (labelText, options, current, onPick) => {
      const label = el("label", null, labelText + " ");
      const s = el("select");
      for (const [value, text] of options) {
        const o = el("option", null, text);
        o.value = value;
        if (value === String(current ?? "")) o.selected = true;
        s.appendChild(o);
      }
      s.onchange = () => { onPick(s.value); load(); };
      label.appendChild(s);
      return label;
    };
    const numInput = (labelText, current, attrs, onPick) => {
      const label = el("label", null, labelText + " ");
      const i = el("input");
      i.type = "number"; Object.assign(i, attrs);
      i.style.width = "70px";
      if (current != null) i.value = current;
      i.onchange = () => {
        onPick(i.value === "" ? null : Number(i.value)); load();
      };
      label.appendChild(i);
      return label;
    };

    row.appendChild(sel("gw",
      known.coverage.map(c => [String(c.gw),
        `GW${c.gw} · ${c.n_sources} src`]),
      state.gw, v => { state.gw = Number(v); }));
    row.appendChild(sel("source",
      [["all", "all (consensus)"],
       ...known.sources.map(s => [s, s])],
      state.source, v => { state.source = v; }));
    row.appendChild(sel("pos", POSITIONS, state.position ?? "",
      v => { state.position = v || null; }));
    row.appendChild(sel("team",
      [["", "all teams"], ...known.teams.map(t => [t, t])],
      state.team ?? "", v => { state.team = v || null; }));
    row.appendChild(numInput("max £", state.max_price,
      { min: 3.5, max: 16, step: 0.5 }, v => { state.max_price = v; }));
    row.appendChild(numInput("min p(app)", state.min_p_appear,
      { min: 0, max: 1, step: 0.05 }, v => { state.min_p_appear = v; }));

    const dis = el("button", null, "biggest disagreements");
    if (state.sort === "spread") dis.className = "primary";
    dis.title = "sort by cross-source spread (max−min xPts)";
    dis.onclick = () => {
      state.sort = state.sort === "spread" ? "xpts" : "spread";
      load();
    };
    row.appendChild(dis);
    filtersHost.appendChild(row);
  }

  // ---- aggregates strip: stat tiles, not a chart --------------------------
  function renderAggregates(res) {
    const strip = el("div");
    Object.assign(strip.style,
      { display: "flex", gap: "8px", flexWrap: "wrap", margin: "2px 0 12px" });
    const tile = (top, sub) => {
      const t = el("div", "pcard");
      t.appendChild(el("div", "nm", top));
      t.appendChild(el("div", "sub", sub));
      return t;
    };
    for (const t of (res.by_team || []).slice(0, 3)) {
      strip.appendChild(tile(`${t.team} ${fmt2(t.avg_xpts)}`,
        `team avg xPts · ${t.n_players} players`));
    }
    for (const p of (res.by_position || []).slice(0, 3)) {
      strip.appendChild(tile(`${p.pos} ${fmt2(p.avg_xpts)}`,
        `position avg xPts · ${p.n_players} players`));
    }
    const src = res.mode === "source" ? `source: ${res.source}`
      : `consensus of ${res.sources.length} sources`;
    strip.appendChild(tile(`GW${res.gw}`, src));
    aggHost.appendChild(strip);
  }

  // ---- the table ----------------------------------------------------------
  function renderTable(res) {
    tableHost.textContent = "";
    const rows = res.rows || [];
    const consensus = res.mode === "consensus";
    const maxX = Math.max(...rows.map(r => r.xpts), 0.01);
    const maxSpread = Math.max(...rows.map(r => r.spread ?? 0), 0.01);

    const columns = [
      { key: "name", label: "player", render: r => {
          const s = el("span", null, r.name);
          s.dataset.code = r.code;
          if (r.status && r.status !== "a") {
            s.appendChild(document.createTextNode(" "));
            s.appendChild(el("span", "chip warn", r.status));
          }
          return s;
        } },
      { key: "team", label: "team" },
      { key: "pos", label: "pos" },
      { key: "price", label: "price", num: true,
        render: r => el("span", null, fmtPrice(r.price)) },
      { key: "xpts", label: consensus ? "xPts (mean)" : "xPts", num: true,
        render: r => bar(r.xpts, maxX, fmt2(r.xpts)) },
    ];
    if (consensus) {
      columns.push(
        { key: "spread", label: "spread", num: true, render: r => {
            if (r.spread == null) return el("span", null, "–");
            // The range and the number, always printed beside the tint.
            return bar(r.spread, maxSpread,
              `${fmt2(r.spread)}  (${fmt1(r.xpts_min)}–${fmt1(r.xpts_max)})`);
          } },
        { key: "n_sources", label: "src", num: true },
      );
    } else {
      columns.push({ key: "xp_if_appears", label: "xPts if plays", num: true,
        render: r => el("span", null, fmt2(r.xp_if_appears)) });
    }
    columns.push(
      { key: "xmins", label: "xMins", num: true,
        render: r => el("span", null, fmt1(r.xmins)) },
      { key: "p_appear", label: "p(appear)", num: true,
        render: r => el("span", null, fmt2(r.p_appear)) },
      { key: "own_pct", label: "owned %", num: true,
        render: r => el("span", null, fmt1(r.own_pct)) },
      { key: "value", label: "value", num: true,
        render: r => el("span", null, fmt2(r.value)) },
    );

    const wrap = dataTable(columns, rows, tableHost);
    wrap.addEventListener("click", e => {
      const tr = e.target.closest("tbody tr");
      const tagged = tr && tr.querySelector("[data-code]");
      if (!tagged) return;
      state.detail_code = Number(tagged.dataset.code);
      load();
    });
    tableHost.appendChild(el("p", "sub",
      `${res.row_count} players · GW${res.gw} · sorted by ${res.sort} — ` +
      "click a row to compare sources"));
  }

  // ---- per-player source comparison ---------------------------------------
  function renderDetail(detail) {
    detailHost.textContent = "";
    if (!detail) return;
    const head = el("div", "filters");
    head.appendChild(el("b", null,
      `${detail.name} — every source, GW${detail.gw_from}–GW${detail.gw_to}`));
    if (detail.outlier) {
      const o = el("span", "chip warn",
        `outlier: ${detail.outlier.source} ` +
        `${detail.outlier.delta_vs_rest > 0 ? "+" : ""}` +
        `${fmt2(detail.outlier.delta_vs_rest)} vs rest at GW${detail.outlier.gw}`);
      head.appendChild(o);
    }
    const close = el("button", null, "close");
    close.onclick = () => { state.detail_code = null; load(); };
    head.appendChild(close);
    detailHost.appendChild(head);

    const gws = [...new Set(detail.rows.map(r => r.gw))].sort((a, b) => a - b);
    const sources = [...new Set(detail.rows.map(r => r.source))].sort();
    const at = new Map(detail.rows.map(r => [`${r.source}|${r.gw}`, r]));

    const columns = [{ key: "source", label: "source", render: r => {
      const s = el("span", null, r.source);
      if (detail.outlier && detail.outlier.source === r.source) {
        s.appendChild(document.createTextNode(" "));
        s.appendChild(el("span", "chip warn", "outlier"));
      }
      return s;
    } }];
    for (const g of gws) {
      columns.push({ key: `gw${g}`, label: `gw${g} xpts`, num: true,
        render: r => {
          const cell = at.get(`${r.source}|${g}`);
          if (!cell || cell.xpts == null) return el("span", null, "–");
          const bits = [fmt2(cell.xpts)];
          if (cell.xmins != null) bits.push(`${fmt1(cell.xmins)}'`);
          if (cell.p_appear != null) bits.push(`p ${fmt2(cell.p_appear)}`);
          return el("span", null, bits.join(" · "));
        } });
    }
    const rows = sources.map(s => {
      const row = { source: s };
      for (const g of gws) row[`gw${g}`] = at.get(`${s}|${g}`)?.xpts ?? null;
      return row;
    });
    dataTable(columns, rows, detailHost);
    detailHost.appendChild(el("p", "sub",
      "each cell: xPts · expected minutes · p(appear) where the source " +
      "publishes them — kept separate, never multiplied together"));
  }

  await load();
}
