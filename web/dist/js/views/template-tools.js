/* Squad-vs-field diff and the what-if exposure simulator.
 *
 * Owned separately from template.js so the two halves of the Template tab can
 * be built in parallel. template.js calls renderTools(host, ctx) once, after
 * its own cards; everything below that call belongs to this module.
 *
 * ctx is the seam. It carries, at minimum:
 *   ctx.res      the full ownership_eo payload (fields[], rows[], squad, ...)
 *   ctx.fieldKey the field the page is currently measuring against
 *   ctx.measure  "eo" | "own"
 *   ctx.onFocus  (code) => void   ask the page to open its player drawer
 * template.js re-invokes renderTools whenever the selection changes, so this
 * module renders from ctx and holds no cross-render state of its own.
 */

export function renderTools(host, ctx) {
  void ctx;
  host.textContent = "";
}

export default renderTools;
