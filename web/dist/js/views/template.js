/* Template & effective ownership: one panel call (ownership_eo) feeds four
   cards. The header explains EO and says which metrics are live vs stale;
   the stale 2025-26 GW38 top10k/elite rows render ONLY inside a labelled
   <details> — never merged into the current template (the audit's trap 2). */

import { runPanel, el, emptyBox, errBox, provenance, dataTable,
         fmtPrice, fmt1 } from "/js/app.js";

function card(title, sub) {
  const c = el("section", "card");
  c.appendChild(el("h2", null, title));
  if (sub) c.appendChild(el("p", "sub", sub));
  return c;
}

/* single-hue tint bar with the number always printed (never color alone) */
function eoBar(value, max) {
  const span = el("span");
  const b = el("span", "bar");
  b.style.width = `${Math.max(2, Math.round(46 * (value / (max || 1))))}px`;
  span.append(b, document.createTextNode(`${value.toFixed(1)}%`));
  return span;
}

function coverChip(r) {
  if (r.in_squad === true) return el("span", "chip good", "yours");
  if (r.in_squad === false) return el("span", "chip", "not owned");
  return el("span", null, "–"); // squad unreadable: no claim either way
}

const pct = v => v == null ? "–" : `${Number(v).toFixed(1)}%`;

function renderExplain(res, host) {
  const p = el("p", null,
    "Effective ownership (EO) is the share of a cohort effectively holding a " +
    "player once captaincy is counted: 60% owned + half of them captaining " +
    "= 90% EO, so values above 100% are normal for premium captains. " +
    "“FPL own %” beside it is marginal ownership — no captaincy weighting.");
  host.appendChild(p);
  const chips = el("div", "filters");
  for (const c of res.gws_covered || []) {
    chips.appendChild(el("span", c.live ? "chip good" : "chip warn",
      `${c.metric} · ${c.season} GW${c.gw} · ${c.live ? "live" : "last season"}`));
  }
  if (chips.childNodes.length) host.appendChild(chips);
  if (res.metrics_note) host.appendChild(el("p", "sub", res.metrics_note));
  if (res.cohort_note) host.appendChild(el("p", "sub", res.cohort_note));
}

function templateColumns(res, maxEo) {
  const cols = [
    { key: "name", label: "player" },
    { key: "pos", label: "pos" },
    { key: "team", label: "team" },
    { key: "price", label: "price", num: true,
      render: r => el("span", null, fmtPrice(r.price)) },
    { key: "eo_pred_pct", label: `EO predicted`, num: true,
      render: r => r.eo_pred_pct == null ? el("span", null, "–")
                                         : eoBar(r.eo_pred_pct, maxEo) },
    { key: "own_pct", label: "FPL own %", num: true,
      render: r => el("span", null, pct(r.own_pct)) },
  ];
  if ((res.rows || []).some(r => r.elite_own_pct != null)) {
    // The payload keys are historically named elite_*, but the panel reports
    // whichever cohort `res.cohort` names -- it can be top1k. Hardcoding
    // "elite" here printed top1k numbers under the word elite, which is the
    // same mislabelling the panel itself was just fixed for. Take the label
    // from the data.
    const co = res.cohort || "cohort";
    cols.push(
      { key: "elite_own_pct", label: `${co} own %`, num: true,
        render: r => el("span", null, pct(r.elite_own_pct)) },
      { key: "elite_eo_pct", label: `${co} EO %`, num: true,
        render: r => el("span", null, pct(r.elite_eo_pct)) },
    );
  }
  cols.push(
    { key: "xpts", label: res.xpts_gw != null ? `xPts gw${res.xpts_gw}` : "xPts",
      num: true, render: r => el("span", null, fmt1(r.xpts)) },
    { key: "in_squad", label: "yours", render: coverChip },
  );
  return cols;
}

function renderTemplate(res, host) {
  const rows = res.rows || [];
  if (!rows.length) return host.appendChild(emptyBox("no template rows"));
  const maxEo = Math.max(...rows.map(r => r.eo_pred_pct ?? 0), 1);
  dataTable(templateColumns(res, maxEo), rows, host);

  const readable = rows.some(r => r.in_squad != null);
  if (readable) {
    const missing = rows.filter(r => r.in_squad === false).slice(0, 12);
    const line = el("div", "filters");
    line.appendChild(el("label", null,
      missing.length ? "template you're missing:" : "you hold the whole template shown"));
    for (const m of missing) {
      line.appendChild(el("span", "chip warn",
        `${m.name} ${m.eo_pred_pct != null ? pct(m.eo_pred_pct) : pct(m.own_pct)}`));
    }
    host.appendChild(line);
  }
  if (res.squad_note) host.appendChild(el("p", "sub", res.squad_note));
}

function renderDiffs(res, host) {
  const all = res.differentials || [];
  if (!all.length) {
    return host.appendChild(emptyBox("no differential rows",
      "needs consensus projections — run the projections ingest"));
  }
  const filters = el("div", "filters");
  const lab = el("label", null, "min consensus xPts");
  const input = el("input");
  input.type = "number"; input.step = "0.5"; input.min = "0"; input.value = "3";
  lab.appendChild(input);
  filters.appendChild(lab);
  const count = el("label", null, "");
  filters.appendChild(count);
  host.appendChild(filters);

  const slot = el("div");
  host.appendChild(slot);
  const cols = [
    { key: "name", label: "player" },
    { key: "pos", label: "pos" },
    { key: "team", label: "team" },
    { key: "price", label: "price", num: true,
      render: r => el("span", null, fmtPrice(r.price)) },
    { key: "own_pct", label: "FPL own %", num: true,
      render: r => el("span", null, pct(r.own_pct)) },
    { key: "eo_pred_pct", label: "EO predicted", num: true,
      render: r => el("span", null, pct(r.eo_pred_pct)) },
    { key: "xpts", label: res.xpts_gw != null ? `xPts gw${res.xpts_gw}` : "xPts",
      num: true, render: r => el("span", null, fmt1(r.xpts)) },
    { key: "xpts_spread", label: "source spread", num: true,
      render: r => el("span", null, fmt1(r.xpts_spread)) },
    { key: "in_squad", label: "yours", render: coverChip },
  ];
  const rerender = () => {
    const min = Number(input.value) || 0;
    // the classic quadrant order: cheapest-owned first, best xPts breaking ties
    const rows = all.filter(r => (r.xpts ?? 0) >= min)
      .sort((a, b) => (a.own_pct ?? 0) - (b.own_pct ?? 0) || (b.xpts ?? 0) - (a.xpts ?? 0));
    slot.textContent = "";
    count.textContent = `${rows.length} of ${all.length} shown`;
    if (!rows.length) slot.appendChild(emptyBox(`no differentials at ≥ ${min} xPts`));
    else dataTable(cols, rows, slot);
  };
  input.oninput = rerender;
  rerender();
}

function renderLastSeason(ls) {
  const d = el("details", "card");
  const s = el("summary", null,
    `Last season's final template — ${ls.season} GW${ls.gw} (stale: the old ` +
    `season's end state, NOT current EO)`);
  s.style.cursor = "pointer";
  d.appendChild(s);
  const rows = ls.rows || [];
  const body = el("div");
  d.appendChild(body);
  dataTable([
    { key: "name", label: "player" },
    { key: "pos", label: "pos" },
    { key: "team", label: "team" },
    { key: "eo_top10k_pct", label: "EO top10k", num: true,
      render: r => el("span", null, pct(r.eo_top10k_pct)) },
    { key: "eo_elite_pct", label: "EO elite", num: true,
      render: r => el("span", null, pct(r.eo_elite_pct)) },
  ], rows, body);
  body.appendChild(el("p", "sub",
    "Where last season's sharpest cohorts finished — context only. " +
    "It never feeds the current template table above."));
  return d;
}

export default async function view(host) {
  const explain = card("Effective ownership, explained",
    "What EO means, and which metrics are live versus last season's.");
  const template = card("The template",
    "Most-owned by the best available cohort, your coverage marked.");
  const diffs = card("Differentials",
    "High consensus xPts, low ownership — the classic quadrant.");
  host.append(explain, template, diffs);

  let res, prov;
  try {
    ({ result: res, provenance: prov } = await runPanel("ownership_eo", {}));
  } catch (e) {
    template.appendChild(errBox(e));
    return;
  }
  if (res?.empty) {
    template.appendChild(emptyBox(res.reason));
    return;
  }
  renderExplain(res, explain);
  renderTemplate(res, template);
  renderDiffs(res, diffs);
  if (res.last_season?.rows?.length) host.appendChild(renderLastSeason(res.last_season));
  template.appendChild(provenance(prov));
}
