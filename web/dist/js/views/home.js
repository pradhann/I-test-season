/* Dashboard: squad pitch, price movers, bookmaker market watch, ideas.
   Ports the original single-page panels onto the shell, bug-fixes included:
   price radar renders its real risers/fallers shape, idea chips use the
   panel's own hit/miss vocabulary, and sorting never eats the provenance. */

import { runPanel, el, emptyBox, errBox, provenance, dataTable,
         fmtPrice, fmt1, fmt2, bar } from "/js/app.js";

function card(title, sub) {
  const c = el("section", "card");
  c.appendChild(el("h2", null, title));
  if (sub) c.appendChild(el("p", "sub", sub));
  return c;
}

async function panelInto(host, script, params, render) {
  try {
    const { result, provenance: prov } = await runPanel(script, params);
    if (result?.empty) host.appendChild(emptyBox(result.reason));
    else render(result, host);
    host.appendChild(provenance(prov));
  } catch (e) { host.appendChild(errBox(e)); }
}

function pcard(p) {
  const d = el("div", "pcard" + (p.is_captain ? " cap" : ""));
  d.appendChild(el("div", "nm",
    p.name + (p.is_captain ? " (C)" : p.is_vice ? " (V)" : "")));
  d.appendChild(el("div", "sub",
    `${fmtPrice(p.price)}${p.xpts != null ? " · " + fmt1(p.xpts) : ""}`));
  if (p.flag) d.appendChild(el("div", "chip warn", p.flag));
  return d;
}

function renderSquad(res, host) {
  const pitch = el("div", "pitch");
  const byPos = { GKP: [], DEF: [], MID: [], FWD: [] };
  for (const p of res.starters || []) (byPos[p.pos] || byPos.MID).push(p);
  for (const posRow of ["GKP", "DEF", "MID", "FWD"]) {
    if (!byPos[posRow].length) continue;
    const row = el("div", "row");
    byPos[posRow].forEach(p => row.appendChild(pcard(p)));
    pitch.appendChild(row);
  }
  host.appendChild(pitch);
  const bench = el("div", "bench");
  (res.bench || []).forEach(p => bench.appendChild(pcard(p)));
  host.appendChild(bench);
  const line = el("p", "sub");
  line.textContent =
    `source: ${res.provenance_source || "?"} · bank ${fmtPrice((res.bank_tenths ?? 0) / 10)}` +
    (res.projected_xi_xpts != null ? ` · projected XI ${fmt1(res.projected_xi_xpts)} pts` : "");
  host.appendChild(line);
}

function renderMovers(res, host) {
  const mk = (label, rows, cls) => {
    const d = el("div");
    d.appendChild(el("h2", null, label));
    if (!rows?.length) { d.appendChild(el("p", "sub", "none in window")); return d; }
    dataTable([
      { key: "name", label: "player" },
      { key: "team", label: "team" },
      { key: "net", label: "net transfers", num: true,
        render: r => el("span", null, (r.net ?? 0).toLocaleString()) },
      { key: "net_per_hour", label: "per hour", num: true,
        render: r => el("span", null, Math.round(r.net_per_hour ?? 0).toLocaleString()) },
      { key: "price", label: "price", num: true,
        render: r => el("span", null, fmtPrice(r.price)) },
      { key: "own_pct", label: "owned %", num: true },
    ], rows, d);
    return d;
  };
  const wrap = el("div", "grid two");
  wrap.appendChild(mk("Risers", res.risers, "good"));
  wrap.appendChild(mk("Fallers", res.fallers, "bad"));
  host.appendChild(wrap);
  if (res.window) host.appendChild(
    el("p", "sub", `window ${res.window.from} → ${res.window.to}`));
}

function renderMarket(res, host) {
  const rows = res.rows || [];
  if (!rows.length) return host.appendChild(emptyBox("no derived odds rows"));
  const max = Math.max(...rows.map(r => r.value));
  dataTable([
    { key: "name", label: "team/player" },
    { key: "market", label: "market" },
    { key: "value", label: "probability", num: true,
      render: r => bar(r.value, max, fmt2(r.value)) },
    { key: "spread", label: "method spread", num: true,
      render: r => el("span", null, r.spread == null ? "–" : fmt2(r.spread)) },
    { key: "fixture", label: "fixture" },
  ], rows, host);
  if (res.coverage) host.appendChild(el("p", "sub", res.coverage));
}

function renderIdeas(res, host) {
  const rows = res.rows || [];
  if (!rows.length) return host.appendChild(emptyBox("no ideas tracked yet"));
  dataTable([
    { key: "idea_id", label: "id" },
    { key: "text", label: "idea",
      render: r => el("span", null, (r.text || "").slice(0, 90)) },
    { key: "status", label: "status",
      render: r => el("span", "chip", r.status || "open") },
    { key: "outcome", label: "outcome", render: r => {
        if (!r.outcome) return el("span", null, "–");
        // hit/miss is the panel's vocabulary — not correct/incorrect
        const cls = r.outcome === "hit" ? "chip good"
          : r.outcome === "miss" ? "chip bad" : "chip";
        return el("span", cls, r.outcome);
      } },
    { key: "gw", label: "gw", num: true },
  ], rows, host);
  if (res.summary) host.appendChild(el("p", "sub",
    `open ${res.summary.open ?? "?"} · hit rate ${res.summary.hit_rate ?? "–"}`));
}

export default async function home(host) {
  const squad = card("My squad", "Your 15, priced and flagged, with where the squad was read from.");
  const movers = card("Price radar", "Net transfer flow since the last snapshot window.");
  const market = card("Market watch", "Bookmaker-implied probabilities, de-vigged; spread across methods.");
  const ideas = card("Ideas", "Your tracked hypotheses and how they resolved.");
  host.append(squad, movers, market, ideas);
  await Promise.all([
    panelInto(squad, "squad_overview", {}, renderSquad),
    panelInto(movers, "price_radar", {}, renderMovers),
    panelInto(market, "market_watch", {}, renderMarket),
    panelInto(ideas, "idea_registry", {}, renderIdeas),
  ]);
}
