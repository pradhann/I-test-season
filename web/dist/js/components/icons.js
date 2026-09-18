/* ONE icon set for the whole app (DESIGN_PRINCIPLES R39, R40, R41).

   Eleven marks, which is the set DESIGN_PRINCIPLES R39 names and the set
   the web contract pins. Each is drawn as
   inline SVG: 14px of geometry on a 16px box, `currentColor`, stroke 1.5, no
   fill. Nothing here is an emoji and nothing here is a box-drawing or
   geometric character standing in for a drawing. Before this file the app
   used three arrow families for the same jobs, three different characters
   for a close control, and two emoji.

   Built with createElementNS rather than an innerHTML string: the shapes are
   literals, but the rule that no markup string is ever assigned into the DOM
   is easier to keep when there are no exceptions to it. */

const NS = "http://www.w3.org/2000/svg";

/* The geometry, on a 16x16 box with 1px of breathing room on each side. */
const PATHS = {
  "sort-up": "M4.5 9.5 8 6l3.5 3.5",
  "sort-down": "M4.5 6.5 8 10l3.5-3.5",
  "sort-none": "M5 7l3-3 3 3M5 9l3 3 3-3",
  "chevron-right": "M6.5 3.5 11 8l-4.5 4.5",
  "chevron-left": "M9.5 3.5 5 8l4.5 4.5",
  "chevron-down": "M3.5 6 8 10.5 12.5 6",
  external: "M9 3h4v4M13 3 7.5 8.5M11 9.5V13H3V5h3.5",
  close: "M4 4l8 8M12 4l-8 8",
  info: "M8 2.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11M8 7.3v4M8 4.9v.2",
  warning: "M8 2.5 14 13H2zM8 6.6v3M8 11.2v.2",
  refresh: "M13 8a5 5 0 1 1-1.7-3.8M13.2 3v2.4h-2.4",
};

/* One mark. `name` is a key of PATHS; an unknown name draws the info mark,
   which is visible and wrong rather than invisible and wrong. */
export function icon(name, cls) {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.5");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", "ic " + (cls || ""));
  const p = document.createElementNS(NS, "path");
  p.setAttribute("d", PATHS[name] || PATHS.info);
  svg.appendChild(p);
  return svg;
}

/* The three sort marks, by the aria-sort value the header carries. */
export function sortIcon(ariaSort) {
  if (ariaSort === "ascending") return icon("sort-up");
  if (ariaSort === "descending") return icon("sort-down");
  return icon("sort-none");
}
