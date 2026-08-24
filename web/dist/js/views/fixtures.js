/* Fixtures ticker: opponents across the horizon, coloured by OUR fitted
   difficulty (sequential single hue via color-mix; the number is always in
   the cell's tooltip and the legend states the scale). Neutral when the
   ratings artefact has no value — never an invented colour. */

import { runPanel, el, emptyBox, errBox, provenance } from "/js/app.js";

export default async function fixtures(host) {
  const cardEl = el("section", "card");
  cardEl.appendChild(el("h2", null, "Fixture ticker"));
  const sub = el("p", "sub");
  cardEl.appendChild(sub);
  host.appendChild(cardEl);
  try {
    const { result: res, provenance: prov } = await runPanel("fixture_ticker", {});
    if (res?.empty) { cardEl.appendChild(emptyBox(res.reason)); return; }
    const teams = res.teams || [];
    if (!teams.length) { cardEl.appendChild(emptyBox("no fixtures")); return; }

    const gws = [...new Set(teams.flatMap(t => (t.fixtures || []).map(f => f.gw)))]
      .sort((a, b) => a - b);
    const anyDifficulty = teams.some(t => (t.fixtures || []).some(
      f => (f.opponents || []).some(o => o.difficulty != null)));
    sub.textContent = anyDifficulty
      ? "Colour = our fitted Dixon-Coles difficulty (dark = hard). "
        + (res.ratings_note || "")
      : "No difficulty artefact present — schedule only, uncoloured. "
        + (res.ratings_note || "");

    const wrap = el("div", "scroll-x");
    const table = el("table", "data");
    const thead = el("thead"); const tr = el("tr");
    tr.appendChild(el("th", null, "team"));
    gws.forEach(g => tr.appendChild(el("th", null, `GW${g}`)));
    thead.appendChild(tr); table.appendChild(thead);
    const tbody = el("tbody");

    for (const t of teams) {
      const row = el("tr");
      row.appendChild(el("td", null, t.team));
      const byGw = {};
      for (const f of t.fixtures || []) (byGw[f.gw] ||= []).push(...(f.opponents || []));
      for (const g of gws) {
        const td = el("td");
        for (const o of byGw[g] || []) {
          const cell = el("span", "chip",
            `${o.opponent}${o.is_home ? "" : " (a)"}`);
          if (o.difficulty != null) {
            const pct = Math.round(o.difficulty * 100);
            cell.style.background =
              `color-mix(in oklab, var(--s1) ${pct}%, var(--surface))`;
            cell.style.color = pct > 55 ? "#fff" : "var(--ink)";
            cell.style.borderColor = "transparent";
            cell.title = `difficulty ${o.difficulty.toFixed(2)}`;
          }
          td.appendChild(cell);
          td.appendChild(document.createTextNode(" "));
        }
        if (!(byGw[g] || []).length) td.textContent = "–";
        row.appendChild(td);
      }
      tbody.appendChild(row);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    cardEl.appendChild(wrap);
    cardEl.appendChild(provenance(prov));
  } catch (e) { cardEl.appendChild(errBox(e)); }
}
