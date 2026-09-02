/* The pizza chart — one player's per-90 percentiles vs same-position peers,
   rendered from the `player_radar` panel inside the shared player drawer.

   THE RULES THE RENDERING KEEPS:
   - Colour = metric family only (--s1 threat / --s3 creation / --s2
     defending, at 78% mix). The radius already encodes the value; a value
     ramp on top would say the same thing twice in two vocabularies.
   - Percentile method, peer count and minutes floor PRINT on the chart —
     a percentile whose population is unstated is a decoration.
   - The four states are visually distinct: full; below-floor (faded, with a
     banner, numbers still printed — hiding true small-sample facts is a
     different dishonesty); no rows (the panel's own reason, no fetch button:
     there is no sanctioned on-demand route for this table); zero-separation
     (hatched at 50 — "no separation yet" is a different word from "short").
   - Overlay-ready by construction: the SVG ships a `pz-fill` group for the
     subject and an empty `pz-cmp` layer; a second player would draw as an
     outline polygon with vertex dots (--s4), colour switching to the PLAYER
     entity — two colour systems never active at once. Cross-position overlay
     is refused with a named reason: different peer groups, percentiles not
     comparable. The "+ compare" chip is reserved and disabled until built.
   - The pizza never mentions Understat. Two sources, two sections, two
     clocks; the Understat section keeps its own fetch-on-click gap. */

import { runPanel, el, errBox } from "/js/app.js";

const NS = "http://www.w3.org/2000/svg";
const GROUP_VAR = { threat: "--s1", creation: "--s3", defending: "--s2" };
const GROUP_LABEL = { threat: "threat", creation: "creation", defending: "defending" };

let uid = 0;

function sv(tag, attrs = {}) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  return n;
}
/* English ordinal: 1st, 2nd, 3rd, 4th … 11th–13th stay -th (never "72th") */
function ord(n) {
  const v = Math.abs(Math.round(Number(n)));
  const m100 = v % 100, m10 = v % 10;
  const suf = m100 >= 11 && m100 <= 13 ? "th"
    : m10 === 1 ? "st" : m10 === 2 ? "nd" : m10 === 3 ? "rd" : "th";
  return `${n}${suf}`;
}

const rad = (deg) => (deg - 90) * Math.PI / 180;
const px = (cx, r, deg) => cx + r * Math.cos(rad(deg));
const py = (cy, r, deg) => cy + r * Math.sin(rad(deg));

function wedgePath(cx, cy, r0, r1, a0, a1) {
  const large = a1 - a0 > 180 ? 1 : 0;
  return [
    `M ${px(cx, r0, a0).toFixed(2)} ${py(cy, r0, a0).toFixed(2)}`,
    `L ${px(cx, r1, a0).toFixed(2)} ${py(cy, r1, a0).toFixed(2)}`,
    `A ${r1} ${r1} 0 ${large} 1 ${px(cx, r1, a1).toFixed(2)} ${py(cy, r1, a1).toFixed(2)}`,
    `L ${px(cx, r0, a1).toFixed(2)} ${py(cy, r0, a1).toFixed(2)}`,
    `A ${r0} ${r0} 0 ${large} 0 ${px(cx, r0, a0).toFixed(2)} ${py(cy, r0, a0).toFixed(2)}`,
    "Z",
  ].join(" ");
}

/* Pure renderer: takes [{code, name, series}] where series is the panel
   result. One player = family-hued fills. A second player would draw into
   `pz-cmp` as an outline polygon (fill vs stroke, never two fills) and the
   colour system would switch to the player entity — the guard and the layer
   exist now so the overlay lands without reshaping the SVG. */
export function renderPizza(players) {
  const wrap = el("div", "pz-wrap");
  if (!players.length) return wrap;
  const positions = new Set(players.map(p => p.series.pos));
  if (positions.size > 1) {
    // same-position guard: named refusal, never a silently wrong chart
    const g = el("div", "empty");
    g.appendChild(el("b", null, "Overlay refused."));
    g.appendChild(document.createTextNode(
      "Different peer groups — percentiles are not comparable across "
      + "positions."));
    wrap.appendChild(g);
    return wrap;
  }
  const res = players[0].series;
  const slices = res.slices || [];
  const n = slices.length;
  const W = 320, cx = 160, cy = 152, R = 108, r0 = Math.round(R * 0.18);
  const id = `pz${++uid}`;

  const svg = sv("svg", { viewBox: `0 0 ${W} 300`, class: "pz-svg", role: "img" });
  svg.setAttribute("aria-label",
    `${res.name}: per-90 percentiles vs ${res.n_peers} qualifying ${res.pos}s`);

  // hatch for zero-separation slices
  const defs = sv("defs");
  const pat = sv("pattern", { id, width: 6, height: 6,
                              patternUnits: "userSpaceOnUse",
                              patternTransform: "rotate(45)" });
  const patLine = sv("line", { x1: 0, y1: 0, x2: 0, y2: 6 });
  patLine.setAttribute("class", "pz-hatchline");
  pat.appendChild(patLine);
  defs.appendChild(pat);
  svg.appendChild(defs);

  // guide rings at 25/50/75 — the 50 ring stronger: the median is the reference
  for (const g of [25, 50, 75]) {
    svg.appendChild(sv("circle", {
      cx, cy, r: r0 + (R - r0) * g / 100,
      class: "pz-ring" + (g === 50 ? " pz-ring50" : ""),
    }));
  }
  svg.appendChild(sv("circle", { cx, cy, r: R, class: "pz-ring pz-rim" }));

  const step = 360 / n;
  const gap = Math.min(2.2, 14 / n);
  const fillG = sv("g", { class: "pz-fill" });

  slices.forEach((s, i) => {
    const a0 = i * step + gap / 2, a1 = (i + 1) * step - gap / 2;
    const mid = (a0 + a1) / 2;
    const r1 = r0 + (R - r0) * Math.max(0, Math.min(100, s.percentile)) / 100;
    const path = sv("path", { d: wedgePath(cx, cy, r0, r1, a0, a1) });
    path.setAttribute("class", `pz-slice pz-${s.group}`);
    if (s.ties_at_zero) {
      path.setAttribute("fill", `url(#${id})`);
      path.setAttribute("class", "pz-slice pz-tied");
    }
    const tip = sv("title");
    tip.textContent = s.ties_at_zero
      ? `${s.label}: no separation in this metric yet (≥ half the peers tie `
        + `at 0) · ${s.per90}/90`
      : `${s.label.replace(" /90", "")} ${s.per90}/90 · `
        + `${ord(s.percentile)} of ${res.n_peers} ${res.pos}s`;
    path.appendChild(tip);
    fillG.appendChild(path);

    // percentile numeral at the arc end — the number always prints
    const tn = sv("text", { x: px(cx, r1 + 9, mid).toFixed(1),
                            y: (py(cy, r1 + 9, mid) + 3).toFixed(1),
                            "text-anchor": "middle", class: "pz-num" });
    tn.textContent = String(s.percentile);
    fillG.appendChild(tn);

    // metric label outside the rim, muted
    const lx = px(cx, R + 22, mid), ly = py(cy, R + 22, mid);
    const anchor = Math.abs(lx - cx) < 18 ? "middle" : (lx > cx ? "start" : "end");
    const tl = sv("text", { x: lx.toFixed(1), y: (ly + 3).toFixed(1),
                            "text-anchor": anchor, class: "pz-lab" });
    tl.textContent = s.label.replace(" /90", "");
    fillG.appendChild(tl);
  });
  svg.appendChild(fillG);

  // group spans (this is how DEF's attack/defence halves read as halves):
  // a thin labelled arc per contiguous group run, in the family hue
  let runStart = 0;
  for (let i = 1; i <= n; i++) {
    if (i === n || slices[i].group !== slices[runStart].group) {
      const a0 = runStart * step + 1, a1 = i * step - 1;
      const arc = sv("path", {
        d: `M ${px(cx, R + 7, a0).toFixed(2)} ${py(cy, R + 7, a0).toFixed(2)} `
          + `A ${R + 7} ${R + 7} 0 ${a1 - a0 > 180 ? 1 : 0} 1 `
          + `${px(cx, R + 7, a1).toFixed(2)} ${py(cy, R + 7, a1).toFixed(2)}`,
        class: `pz-span pz-span-${slices[runStart].group}`,
      });
      svg.appendChild(arc);
      runStart = i;
    }
  }

  // the empty compare layer — outline polygons with vertex dots land here
  svg.appendChild(sv("g", { class: "pz-cmp" }));

  wrap.appendChild(svg);

  // legend chips — one per family present, text always beside colour
  const legend = el("div", "pz-legend");
  const seen = [...new Set(slices.map(s => s.group))];
  for (const g of seen) {
    const chip = el("span", `pz-key pz-key-${g}`);
    chip.appendChild(el("i"));
    chip.appendChild(document.createTextNode(GROUP_LABEL[g]));
    legend.appendChild(chip);
  }
  wrap.appendChild(legend);
  return wrap;
}

/* The drawer section. Mounted between the per-source projection matrix and
   the Understat profile; returns {cancel} like its neighbours. */
export function radarSection(host, code) {
  const box = el("div", "pz-box");
  const hd = el("div", "pz-hd");
  hd.appendChild(el("h2", null, "Percentiles — per 90, vs position"));
  const cmp = el("button", "chip pz-compare", "+ compare");
  cmp.disabled = true;
  cmp.title = "overlay a second same-position player — reserved, not built yet";
  hd.appendChild(cmp);
  box.appendChild(hd);
  const body = el("div");
  box.appendChild(body);
  host.appendChild(box);

  let cancelled = false;
  (async () => {
    body.appendChild(el("p", "sub", "loading…"));
    let out;
    try {
      out = await runPanel("player_radar", { code: Number(code) });
    } catch (e) {
      if (cancelled) return;
      body.textContent = "";
      body.appendChild(errBox(e));
      return;
    }
    if (cancelled) return;
    body.textContent = "";
    const res = out.result;

    if (res.empty) {
      // no fetch button: there is no sanctioned on-demand route for this
      // table; the gap prompts the pipeline, not the user.
      const g = el("div", "empty");
      g.appendChild(el("b", null, "No per-match rows for this player."));
      g.appendChild(document.createTextNode(res.reason || ""));
      body.appendChild(g);
      return;
    }

    body.appendChild(el("p", "sub",
      `${res.window.matches} match${res.window.matches === 1 ? "" : "es"} · `
      + `${Math.round(res.window.minutes)}′ · vs ${res.n_peers} qualifying `
      + `${res.pos}s · floor ${res.floor_minutes}′ · mid-rank percentiles`));

    if (res.below_floor) {
      const b = el("div", "pz-floorbanner");
      b.textContent =
        `${Math.round(res.window.minutes)}′ played — below the `
        + `${res.floor_minutes}′ floor. Percentiles this thin reorder on one `
        + `match; shown faded, read gently.`;
      body.appendChild(b);
    }

    const chart = renderPizza([{ code: res.code, name: res.name, series: res }]);
    if (res.below_floor) chart.classList.add("pz-belowfloor");
    body.appendChild(chart);

    if (res.groups_note) body.appendChild(el("p", "sub", res.groups_note));

    const how = el("details", "pz-how");
    how.appendChild(el("summary", null, "how this is computed"));
    for (const line of [
      "per-90 = 90 × Σ stat / Σ minutes, season-to-date, over "
        + "fact_player_match_stats (all players, ingested daily).",
      "Peer group = players sharing this player's FPL position this season "
        + `whose minutes clear the floor (${res.floor_minutes}′ = a third of `
        + "the settled season, never below one full match).",
      res.method + ". Ties split; an all-zero metric collapses to 50.",
      "Everything is oriented more-is-better; goals_prevented is already "
        + "signed; nothing is inverted silently. Minutes is never a slice — "
        + "it is the floor and the header line.",
    ]) how.appendChild(el("p", "sub", line));
    body.appendChild(how);

    body.appendChild(el("p", "provenance",
      `fact_player_match_stats · season-to-date · per-90 · mid-rank · `
      + `as of ${res.as_of || "unknown"}`));
  })();

  return { cancel: () => { cancelled = true; } };
}
