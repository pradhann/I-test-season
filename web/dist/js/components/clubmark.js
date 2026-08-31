/* One club, as a mark: the official badge when it loads, a club-coloured
   monogram when it does not.

   The badge is hotlinked from FPL's public CDN, keyed by the same `team_code`
   every panel payload carries. That is a deliberate trade: no ingest, no local
   asset store, no pipeline work — and a hard dependency on someone else's
   host. So the failure path is not decoration, it is the design: the monogram
   renders the identical size with the club's own colours and short name, the
   swap is one class flip on the error event with no layout shift, and a page
   rendered fully offline is complete rather than full of broken-image glyphs.

   The colours live in clubmark.css, keyed by `data-club="{team_code}"` — a
   kit colour is a stable public fact about the club, not a fact this
   warehouse measures, so it flows through neither a panel nor this file. A
   code the map does not know falls to the neutral --raised/--ink pair, so
   next season's promoted clubs render legibly before anyone updates the map.

   Crests appear in exactly five places, three sizes, never larger, and NEVER
   inside a grid cell — the diverging ramp owns all area colour on that page,
   and an offline fallback disc inside the data field would put club colour
   adjacent-and-confusable with difficulty. */

const BADGE_URL = (teamCode) =>
  `https://resources.premierleague.com/premierleague/badges/70/t${teamCode}.png`;

/* The mark. `sizeClass` is one of "s14" | "s20" | "s34" (defaults to s20 —
   the rail size). `label` is the short name shown in the fallback monogram:
   pass what the payload calls the club, never a guess. The box is sized by
   CSS before the image loads, so the badge appearing never reflows its row. */
export function crest(teamCode, label, sizeClass = "s20") {
  const wrap = document.createElement("span");
  wrap.className = `crest ${sizeClass}`;
  if (teamCode != null) wrap.dataset.club = String(Number(teamCode));

  const img = document.createElement("img");
  if (teamCode != null) img.src = BADGE_URL(Number(teamCode));
  img.alt = "";                 // the text beside the mark names the club
  img.loading = "lazy";
  img.decoding = "async";
  // One class flip reveals the monogram underneath; nothing is created or
  // measured at failure time, so an offline page settles in one paint.
  img.addEventListener("error", () => wrap.classList.add("fall"));
  if (teamCode == null) wrap.classList.add("fall");
  wrap.appendChild(img);

  const mg = document.createElement("span");
  mg.className = "mg";
  mg.setAttribute("aria-hidden", "true");
  mg.textContent = String(label || "?").slice(0, 3).toUpperCase();
  wrap.appendChild(mg);
  return wrap;
}
