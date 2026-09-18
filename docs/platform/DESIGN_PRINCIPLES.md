# Platform design principles

Written 2026-09-18 against the live app at `http://localhost:8321` and the
stylesheets and views in `web/dist/`. Every rule below is one sentence, is
testable by a script or a browser measurement, and names the token or the
component that implements it. `docs/platform/UI_AUDIT.md` holds the
measurements these rules are drawn from and the ranked fix list.

The owner's report, verbatim, is the brief:

> "it's so ugly looks amateurish. Have consistent fonts, well aligned. Better
> avatar, icons, simple animations if it helps, without too much latency. The
> projections is an example of what's wrong: why do you have src there, it
> makes it all poorly aligned. The fractal design must be consistent and
> clean, things must be clickable. High quality, easy to use, filters sorts
> for easy analysis, clickable."

Fixtures (`web/dist/js/views/fixtures.js`, `web/dist/fixtures.css`) is the
accepted quality bar and is the worked example wherever a rule has one.

---

## 0. The two defaults the owner has not ruled on

Both are live defaults. The build proceeds on them. Either can be overturned
by one line from the owner, and the cost of overturning is stated.

**D1. Keep the existing UI face, standardise every number on one tabular
mono.** Text stays on the system stack already in `app.css:59`
(`-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial`).
Numbers use `--mono` (`app.css:25`: `ui-monospace, "SF Mono", Menlo,
Consolas, monospace`) with `font-variant-numeric: tabular-nums`. Two families
app-wide, no webfont, no network font request, no flash of unstyled text.
Cost of overturning: a webfont adds a request on first paint and a fallback
metric to match, which rule 16 budgets against.

**D2. Use FPL's own photos and badges for players and clubs.** Player photos
come from `resources.premierleague.com/premierleague/photos/players/110x140/p{code}.png`
(`app.js:78`), club badges from
`resources.premierleague.com/premierleague/badges/70/t{team_code}.png`
(`components/clubmark.js:23`), both keyed by the ids the warehouse already
carries. Cost of overturning: a local asset store plus an ingest job plus a
cache-bust policy for transfers and new signings.

---

## 1. Type

**R1. Six type sizes exist app-wide and a seventh is a bug.** The scale, with
its only use:

| token | px | used for |
|---|---:|---|
| `--t-title` | 19 | the page title in the topbar (`app.css:89`), once per page |
| `--t-figure` | 17 | the single headline number of a card (`.stat .v`, `app.css:171`) |
| `--t-body` | 13 | body text and the `body` default (`app.css:59`) |
| `--t-cell` | 12.5 | every table and grid cell (`table.data`, `app.css:136`) |
| `--t-label` | 11 | column headers, chips, provenance, card eyebrows (`app.css:115, 139, 154, 124`) |
| `--t-micro` | 10.5 | stat keys and toolbar row labels (`app.css:172, 243`) |

Test: a script over `web/dist/*.css` and `web/dist/chat-app/assets/*.css`
collects every `font-size` value and every `px` length inside a `font:`
shorthand, and the distinct set is those six. Today it is 27.

**R2. Text is set in the UI face and every number is set in `--mono` with
tabular figures.** `body` already sets `font-variant-numeric: tabular-nums`
globally (`app.css:64`); the mono face is applied by `.num`
(`app.css:143-144`), `.stat .v`, `.provenance` and `.delta`. Test: for every
element in `#view` whose text matches `^[\s£+\-]*[0-9][0-9.,:%\s£+\-]*$`,
`getComputedStyle().fontFamily` starts with `ui-monospace`.

**R3. Weight carries emphasis, size does not.** 400 for body, 600 for a
column header or a chip, 700 for the one number a card exists to show; no
other weights. Test: the distinct `font-weight` set rendered inside `#view`
is a subset of {400, 600, 700}.

---

## 2. Alignment

**R4. A number column is right-aligned, a text column is left-aligned, and
nothing is centred.** `td.num` and `th.num` already carry
`text-align: right` (`app.css:143`); text cells inherit `text-align: left`
from `app.css:137`. Test: every `td` whose text is numeric has
`textAlign === "right"`, and every `td` whose text is not has
`textAlign === "left"`.

**R5. A column of the same quantity has the same width in every gameweek.**
Two columns holding one decimal place render at the same pixel width, so a
reader's eye tracks straight down. Test: on Projections, the rendered widths
of the GW columns are equal to within 2px. Today GW6 is 43px and GW10 is
106px, which is the defect the owner photographed.

**R6. A header cell carries the column name and nothing else.** No source
count, no age, no unit, no settled tick, no gameweek suffix on a per-gameweek
column. Metadata goes to one of exactly two places: a `title` on the `th`
when it qualifies the whole column, or a marker on the individual cells when
it qualifies some rows and not others (rule 12). Test: the text of every
rendered `th` matches `^[A-Za-z0-9£%()\- ]+$` and contains no `·`, no `|`,
no digit followed by `SRC`, `h`, `d` or `%`.

**R7. The unit lives in the column header or the card footer, once, never in
the cells.** `£` is the price column's whole header (`xpoints.js:647`) and
the cells print `15.6`, not `£15.6`. Test: no cell text contains a unit
character that its header also contains.

---

## 3. Spacing

**R8. Five spacing steps exist and everything is a multiple of one of
them.** `--sp1: 4px`, `--sp2: 8px`, `--sp3: 12px`, `--sp4: 16px`,
`--sp5: 24px`, applied to every `padding`, `margin`, `gap`, `row-gap` and
`column-gap`. Two exceptions, both declared: `1px` for a hairline rule and
`2px` for an optical nudge inside one component. Test: a script over the
stylesheets collects every `px` length in a spacing property and the distinct
positive set is {1, 2, 4, 8, 12, 16, 24}. Today it is 27 positive values plus
4 negative ones.

**R9. A card is `14px 16px` of padding with `16px` below it, and no view
redefines `.card`.** `app.css:112-114` owns it. Test:
`grep -c "\.card\s*{" web/dist/*.css` returns 1, in `app.css`.

---

## 4. Tables

**R10. One table component serves every tabular surface.** `table.data` in
`app.css:136-145`, wrapped in `.scroll-x` (`app.css:135`), with
`table.sticky-first` (`app.css:177-180`) when the first column must stay put.
Test: every `<table>` rendered inside `#view` carries the class `data`.
Today Chat renders 8 tables that do not, and EliteFPL renders 1.

**R11. Every column header is a sortable button that says so.** The `th` is
`role="button"`, `tabindex="0"`, responds to Enter and Space, carries
`aria-sort` of `ascending`, `descending` or `none`, and prints a persistent
glyph: `▲` ascending, `▼` descending, `⇅` on the inactive columns.
`pipelines.js:417-430` and `template.js:2295-2307` already do this and one of
them becomes the shared helper. Test: every rendered `th` inside a
`table.data` has an `aria-sort` attribute, and exactly one has a value other
than `none`.

**R12. A column's first sort direction is set by the column, not by a global
default.** A magnitude column (xPts, price, ownership, points) opens
descending; a name, team or position column opens ascending; a date column
opens newest first. Test: a click on each header from the unsorted state
produces the direction the column declares.

**R13. A cell that is thinner evidence than its neighbours is marked on the
cell, not in the header.** The marker is a 3px dot in `--warn` at the cell's
top-right corner with the count in the cell's `title`, so the column keeps
its width. This is where the Projections source count goes.

**R14. Nulls print as `–` and never as `0`, `-`, blank or `n/a`.** One glyph,
U+2013, in `--faint`. Test: no rendered cell text equals `0` where the
payload value is `null`.

---

## 5. Filters and sorts

**R15. Every filter for a table sits in one `.filters` row directly above
it.** `app.css:303-305` owns the row. A view needing more controls than one
row holds puts the overflow behind one `more filters` disclosure, not on a
second and third row. Test: `#view` contains at most one `.filters` element
per `table.data`, and it is the table's immediately preceding sibling or
inside the same card header.

**R16. A filter states its effect in the row count under the table.** One
line: `N of M players`, plus the named filters that removed the rest. Test:
the line is present whenever any filter is not at its default.

**R17. Filter state survives a tab switch and a reload through the hash
query, and nothing else is persisted.** The router already splits on `?`
(`app.js:200`). Test: setting a filter, navigating away and back restores it.

---

## 6. Clicking and the fractal levels

**R18. A row that opens something is clickable across its whole width, shows
`cursor: pointer`, keeps its `tr:hover` background, takes focus and opens on
Enter.** The convention is `tr[tabindex="0"][role="button"]` with the class
`.rowlink`, which is what `pipelines.js:519` already does. Test: for every
row that has a click handler, `getComputedStyle(tr).cursor === "pointer"`,
`tr.tabIndex === 0`, and the count of rows with a handler equals the count
with `tabindex`.

**R19. A row with no handler shows no pointer and no hover lift.** Test: no
element inside `#view` has `cursor: pointer` without a click handler, a
`tabindex` or an ancestor that has one.

**R20. One drawer implementation serves every level.** `.drawer`
(`app.css:283-289`) at `width: min(560px, 96vw)`, opening from the right in
180ms, closing on Escape and on a click outside, returning focus to the
element that opened it, with `drawerHead(title, sub)` (`creators.js:3405`,
`.dhead` at `app.css:273`) as its header. Test:
`grep -c 'el("aside", "drawer' web/dist/js/**/*.js` returns 1. Today it
returns 4 at three different widths.

**R21. The fractal path is one breadcrumb in the drawer head, and it is
clickable back to every level.** `Creators / Let's Talk FPL / GW4 transfer
tips / Haaland`, each segment before the last opening that level in the same
drawer. Test: the drawer head contains one `.crumb` element whose segment
count equals the depth, with every segment but the last a button.

**R22. A drawer opens one level per click and never two.** Clicking a
creator opens the episode list, not an episode. Test: one click changes the
breadcrumb depth by exactly one.

---

## 7. Time

**R23. Every age renders in days.** `4d`, `10d`, `18d`, and `today` under 24
hours. No `Nh`, no `Nh Mm`, no `Nd Nh`, no bare UTC clock beside body text.
`fmtAge` (`app.js:154`) gains a `fmtAgeDays` sibling and the hour branch of
`fmtSpan` (`app.js:135-146`) stops being reachable from a view. Test: a
regex for `\b\d+\s*h\b` over `#view`'s `innerText` returns nothing on every
tab. Today Pipelines prints 12 and Chat prints `603h`.

**R24. The deadline countdown is the one exception and keeps hours and
minutes.** `.deadline` in the topbar (`app.css:90-94`), because inside 24
hours the minutes are the decision. Test: the only `\d+h` in the document is
inside `#deadline`.

**R25. The exact instant stays reachable in a `title`, never in the visible
text.** `provenance()` (`app.js:53-66`) is the pattern. Test: no rendered
text matches an ISO timestamp.

---

## 8. States

**R26. One empty state.** `emptyBox(reason, hint)` (`app.js:40-46`,
`.empty` at `app.css:126-129`), which always prints why the thing is empty
and what would fill it. Test: `grep -c 'class="empty"\|"empty"'` over the
views finds no hand-rolled construction; every empty region comes from
`emptyBox`.

**R27. One error state, and it is used only for an error.** `errBox(e)`
(`app.js:48`, `.err` at `app.css:130-132`), meaning a request failed or a
panel threw. A stale plan is not an error. Test: `.err` renders only when a
`catch` ran.

**R28. One gap state, for a fact the payload says it does not have.** The
class is `.gap`, a left border in `--warn` on `--raised`, carrying the field
name, the reason from the payload and the action that would fix it. This is
rule 9 of the shared brief made visible: absence is shown as absence, with
the reason. Test: every `.gap` carries a reason string that came from the
payload.

**R29. One stale marker, for data that exists and is older than its
window.** A `.chip.warn` reading `stale · Nd`, placed on the block it
qualifies, with the source and the window in its `title`. Test: a block whose
payload `state` is `stale` renders exactly one `.chip.warn` and no `.err`.

**R30. A loading surface reserves the height its content will take.** A
skeleton at the final row count and row height (`creators.js:320`,
`.cx-skel`), so the arrival of data moves nothing. Test: rule 33.

---

## 9. Colour

**R31. Difficulty is the five FPL FDR steps and nothing else uses them.**
`fixtures.css:53-80` owns the ramp, numbered as FPL numbers it, identical in
both themes. Test: the FDR custom properties are referenced only from
`fixtures.css` and the fixture grid.

**R32. `--good`, `--warn` and `--bad` mean state and never magnitude.**
`app.css:23` reserves them. A heat ramp uses `--s1` alone as a sequential
scale, and a signed delta uses `--good` and `--bad` only for its sign, not
for its size. Test: no `color-mix` whose percentage varies by value is
written against `--good`, `--warn` or `--bad`. Today `xpoints.js:722-724`
does exactly that.

**R33. One accent means selection.** `--accent` (`app.css:21`), the
desaturated steel blue chosen to sit outside the green family so that
selected never reads as positive. Test: `--accent` appears only on active
rail items, `on` segment buttons and the squad ring.

**R34. Colour never carries a fact alone.** Every tinted cell, chip and dot
prints the number or the word beside it. Test: every element with a tinted
background has non-empty text or a `title`.

---

## 10. Avatars and marks

**R35. A player photo renders at one of three sizes and never at a fourth.**
`.avatar` 24px round for a table row (`app.css:234`), `.pcard .face` 64px
tall for a pitch card (`app.css:199`), `.dhead .bigface` 56x68 for a drawer
masthead (`app.css:274`). Test: the distinct rendered sizes of
`img[src*="photos/players"]` are those three.

**R36. A club badge renders through `crest()` and nowhere else.**
`components/clubmark.js:31`, three sizes `s14`, `s20`, `s34`, with a
club-coloured monogram fallback in the identical box so a failed load shifts
nothing. Test: no `<img>` whose `src` contains `badges/` exists outside a
`.crest`. Today `app.js:106-110` builds a second one at `badges/50/` that
removes itself on error, and `fixtures.js:2765` asks for a size class `s16`
that `clubmark.css` does not define.

**R37. A missing photo falls back to the jersey placeholder, never to a
broken-image glyph and never to a collapsed box.** `FACE_FALLBACK`
(`app.js:83-91`). Test: with the CDN blocked, no `#view` element changes
height.

**R38. Photos and badges are served through the app, not hotlinked on every
mount.** A `GET /api/asset/player/{code}.png` and
`GET /api/asset/club/{team_code}.png` proxy with a 7 day
`Cache-Control: public, max-age=604800, immutable`, so a cold Projections
mount costs 100 cached responses rather than 100 third-party round trips.
Test: no `#view` element has a `src` on `resources.premierleague.com`.

---

## 11. Icons

**R39. One icon set, one size, drawn as inline SVG at 14px on a 16px box,
`currentColor`, `stroke-width: 1.5`.** The set is the eleven marks the app
actually needs: sort-up, sort-down, sort-none, chevron-right, chevron-left,
chevron-down, external, close, info, warning, refresh. Test: every `svg`
inside `#view` has `width="16" height="16"`.

**R40. No emoji anywhere in a UI string.** Test: a regex over every rendered
`innerText` and over the view sources for U+1F300 to U+1FAFF and U+2600 to
U+27BF returns nothing. Today `components/chatter.js:546` and `:550` print
`🗣` and `⚙`.

**R41. No box-drawing or geometric character stands in for an icon.** The
three arrow families in use today (`↑↓→↗`, `▲▼▸▾`, `◀▶►◄`) collapse into
the SVG set; `✓`, `✕`, `×`, `★`, `◆`, `○`, `◐`, `●`, `◇`, `▽`, `⊘`, `√`,
`⇄` and `⇅` go with them. Test: the same regex over the view sources returns
only the `–` null glyph and the `≥`, `≤`, `≈` and `Σ` used inside prose and
column names.

---

## 12. Motion

**R42. Four things may animate and nothing else.** The drawer transform
(180ms ease-out, `app.css:287`), a hover lift on a card or pitch card (120ms,
`app.css:225`), a chip or row background on hover (120ms), and a skeleton
shimmer while a panel is in flight. Test: the distinct `transition-property`
set inside `#view` is a subset of {transform, box-shadow, background-color,
border-color, opacity}.

**R43. No animation exceeds 180ms and none delays data.** Content is written
into the DOM before any transition starts; a transition never gates a fetch,
a sort or a filter. Test: every rendered `transition-duration` is at most
180ms, and re-sorting a 100 row table paints in under one frame budget.

**R44. `prefers-reduced-motion: reduce` turns all four off.**
`app.css:289` already does this for the drawer and the rule extends to the
rest. Test: with the media query emulated, every `transition-duration` inside
`#view` is `0s`.

---

## 13. Latency

**R45. A tab paints its data within 1,500ms of navigation on this machine.**
Measured as the `responseEnd` of the last panel call the mount fires, from
`performance.getEntriesByType('navigation')[0].startTime`. Today: Pipelines
1,901ms, Fixtures 2,588ms, EliteFPL 3,395ms, Projections 3,531ms, Dashboard
9,241ms, Creators still loading at 29,102ms.

**R46. A mount fires at most three panel calls and transfers at most
250 KiB.** More than three means the view is doing a join the panel should
do. Test: `performance.getEntriesByType('resource')` filtered to `/api/`
after one clean mount. Today Creators fires 40 calls for 3,821 KiB and
EliteFPL transfers 862 KiB in 4.

**R47. Nothing moves after first paint.** Cumulative layout shift inside
`#view` is 0.00 on a cold cache, which means the skeleton of rule 30 holds
the final geometry and every image box is sized in CSS before it loads.
Test: a `PerformanceObserver` on `layout-shift` with `buffered: true` after a
reload with the image cache cleared.

**R48. A view's code is loaded when its tab is opened, not on every page
load.** `index.html` imports all nine views statically today, so every tab
pays for 850,557 bytes of JS and 245,189 bytes of CSS. Dynamic
`import()` inside the router (`app.js:203`) and one stylesheet per view
loaded beside it. Test: a cold mount of Account requests `account.js` and
`app.js` and no other view module.

**R49. A sort, a filter or a tab switch re-renders from data already held and
issues no request.** Test: `performance.getEntriesByType('resource')` count
does not change across a sort or a filter change.

---

## 14. How these are enforced

Rules R1, R2, R4, R6, R8, R10, R11, R14, R18, R19, R20, R23, R26, R27, R35,
R36, R40, R41 are static and belong in `tests/unit/test_web_contract.py`
beside the tests already there. Rules R5, R30, R43, R45, R46, R47, R48, R49
need the browser and belong in one measurement script that walks all nine
tabs at 1280 wide and prints a table, so a regression shows as a number
moving rather than as an opinion.
