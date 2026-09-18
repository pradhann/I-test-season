# Dashboard bake-off: the compiled critique

> Adversarial review of `dashboard_design_A.md` (the decision ledger),
> `dashboard_design_B.md` (the squad is the page) and `dashboard_design_C.md`
> (the subtraction). Written 2026-09-18. Every number below that contradicts a
> design was measured by this review, and the measurement method is named
> beside it. Nothing here is taken from a design document on trust.

---

## 1. How the claims were checked

Four instruments, all re-run for this review rather than quoted:

1. **The live payload.** `POST /api/scripts/dashboard_brief/run {}` against
   `localhost:8321`, HTTP 200, 23,120 bytes, GW5, entry 4490171, `as_of`
   2026-09-12T18:17:08.996298Z. Parsed with a structural walker that printed
   every key, its type and its length.
2. **The live page.** `http://localhost:8321/#home` in a real browser at an
   emulated 1280x800, walking `#view`'s children with
   `getBoundingClientRect().top + scrollY`, and again after a fresh mount at
   the narrowest width the harness allows.
3. **The source.** `web/dist/js/views/home.js` at 2,344 lines and 101,940
   bytes, `web/dist/dashboard.css` at 558 lines, `web/dist/app.css` at 344
   lines, `fpl_edge/platform/scripts/brief.py` at 2,593 lines. Function
   boundaries read out of the file, not inferred.
4. **The suite.** `uv run pytest -q tests/unit/test_web_contract.py`, 61
   tests, `REAL_PYTEST_EXIT=0`. Every break named in section 6 is therefore
   caused by the rebuild and not pre-existing.

One harness fact that changes what can be promised. **A true 390 CSS pixel
viewport is not reachable here.** Requesting 390x844 returns `innerWidth`
446. C says so in its own section 9.6 and is right. A quotes 446 as "a lower
bound for 390" and is also right. Every narrow figure in all three documents,
and in this one, is a floor.

---

## 2. Claims verified, per design

### 2.1 Design A

| # | A's claim | measured | verdict |
|---|---|---|---|
| A1 | §1.1, fourteen regions, verdict top 806 px, document height 3,573 px at 1280x800 | 14 children, verdict `.card.db-verdict` top **806**, height **383**, `document.documentElement.scrollHeight` **3,573**. All fourteen tops and heights match A's table row for row | **holds, exactly** |
| A2 | §5, at the narrowest width the page is 5,897 px tall, the verdict's first pixel is at 1,685, the squad card alone is 1,734 px | fresh mount at 446: doc **5,897**, verdict top **1,685**, pitch card height **1,734** | **holds, exactly, all three** |
| A3 | §8.1, six contract tests at lines 94, 294, 528, 551, 587, 604, plus 317 and 613 as constraints | `grep -n "^def test_"` puts all eight at those exact lines | **holds** |
| A4 | §8, deletion ranges 353-417, 418-449, 450-465, 810-1046, 1170-1522, 1523-1625, 1626-1943, 1944-2066, 2067-2133, 2163-2231, 2333-2344 | section comments and function boundaries put the blocks at 353-416, 418-448, 450-464, 810-1045, 1170-1522, 1523-1625, 1626-1943, 1944-2066, 2067-2132, 2163-2231, 2332-2344. Every range is right to within one blank line | **holds** |
| A5 | §1.3, 11 bare UTC clocks in three distinct values | `innerText` scan of `#view`: **11** clocks, **3** distinct (`18:17Z`, `05:38Z`, and the panel's own `generated_at`, which ticks) | **holds** |
| A6 | §3.2, "`thresholds` (all 28)" | `len(result.thresholds)` is **21** | **does not hold** |
| A7 | §4 wireframe, "lead 0.20", "0.20 clear of Joao Pedro", "Joao Pedro 5.57" | `best_xi.captain_lead_xpts` is **0.16**; `captain_candidates[1].xpts` is **5.609**; the served name is **João Pedro** | **does not hold** |
| A8 | §1.3, 14 ages in five shapes including `11h 32m` | inside `#view`: **13** ages in **four** shapes (`10d`, `5d 12h`, `9d 23h`, `1m`). The `11h 32m` countdown lives in the shell topbar, outside `#view` | **partly holds**, the count mixes a shell element into a view scan |

A's measurement discipline is the best of the three. Two of its three failures
are cosmetic transcription. A6 matters because A's field map promises to place
28 gates in working blocks and only 21 exist.

### 2.2 Design B

| # | B's claim | measured | verdict |
|---|---|---|---|
| B1 | §1.8, the brief names only five of the fifteen, and the five are Kinsky, Verbruggen, B.Fernandes, Isak, João Pedro | a walk over every `{code, name}` pair in the payload joined against `squad_projection`'s fifteen codes returns **5 of 15**, and they are exactly those five. The ten unnamed are exactly B's list | **holds, exactly** |
| B2 | §1.7, seventeen distinct font sizes in `dashboard.css`, twelve in `app.css`, union nineteen | `dashboard.css` **17** (7.5, 9, 9.5, 10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5, 15, 16, 17, 18, 20, 22, exactly B's list); `app.css` **12**; union **19** | **holds, exactly** |
| B3 | §1.7, nineteen distinct padding, margin and gap values in `dashboard.css`, from 1px to 64px | **18** distinct values, 1px to **60px** | **does not hold** |
| B4 | §5.3, `brief.py:235` builds `squad15 = starters + bench`, `_ref()` at `brief.py:98-108` is the serialiser | `squad15 = starters + bench` is at **brief.py:934**; `def _ref` is at **brief.py:797-807**. Both exist and do what B says, at different lines | **substance holds, line numbers do not** |
| B5 | §5.3, `_ref()` plus "the four squad fields `_PLAYER_REF` does not carry" | `_ref` returns code, name, pos, team, team_code, price, own_pct. The additional fields B's `squad` array needs are xpts, p_haul, status, news, is_captain, is_starter, which is **six** | **does not hold** |
| B6 | §6.1, "Sixteen" clock-carrying fields, then a list | the list as written holds **24** entries | **does not hold** |
| B7 | §6.3, `brief.py` computes `as_of` as the minimum of `sources_as_of` excluding `solve_plan` | served `as_of` 2026-09-12T18:17:08.996298Z equals the minimum of the four non-solver clocks | **holds** |
| B8 | §5.2, `squad_overview` serves name, pos, team_code, xpts, status, news, is_captain, price on `starters[]` and `bench[]` | `POST /api/scripts/squad_overview/run` returns 11 starters and 4 bench, each with code, name, pos, team, team_code, price, own_pct, is_starter, is_captain, is_vice, multiplier, status, news, xpts, p_haul | **holds** |
| B9 | §7 drawer, lead 0.16, candidates 5.77 / 5.61 / 4.47, haul 17.7 / 4.9 / 8.9 percent | lead **0.16**; xpts 5.769, 5.609, 4.468; p_haul 0.177, 0.0485, 0.0895 | **holds, every figure** |

B is the only design that gets the captain numbers right. B is also the only
design whose central structural finding (B1) survives verification untouched,
and that finding is the one that decides the bake-off.

### 2.3 Design C

| # | C's claim | measured | verdict |
|---|---|---|---|
| C1 | §1.6, `grep -n suggested_xi web/dist/js/views/home.js` returns nothing | `grep -c` returns **0** | **holds** |
| C2 | §1.4, the duplicate-string table: `public picks` 4, the priced-against sentence 3, `a deadline has passed since ,` 3, `best XI by consensus xPts` 3, `haul odds` 3, `Regenerate` 3, `B.Fernandes 5.8` 3, `close call` 2, `Re-run solve` 2, `5d 12h` 6, `18:17Z` 8 | substring counts over `#view`'s `innerText`: **4, 3, 3, 3, 3, 3, 3, 2, 2, 6, 8**. Eleven of eleven | **holds, exactly** |
| C3 | §1.3, eighteen distinct padding, margin and gap values in `dashboard.css`: 1 2 3 4 5 6 7 8 9 10 12 14 16 18 24 28 30 60 | **18**, and the set is character for character C's set | **holds, exactly** |
| C4 | §1.6, `thresholds` carries 21 gates, the view reads four | `len(result.thresholds)` **21** | **holds** |
| C5 | §1.1, "The page is 3,466 pixels tall" | **3,573**. C's own region table ends at top 3,451 plus height 82, which is 3,533, so the figure does not agree with C's own rows either | **does not hold** |
| C6 | §4.2, at 446 the page is 5,687 px tall, the verdict begins at 1,685 | doc **5,897**, verdict **1,685**. The verdict figure holds, the height does not | **half holds** |
| C7 | §1.5, the broken sentence is caused by `noDash()` at `home.js:155-160` rewriting an em-dash at print time, because `brief.py` serves a reason containing one | the **raw payload** already reads `...a deadline has passed since ,  its moves were priced...`. It contains **zero** U+2014. Its only dash is U+2013 inside `GW4–8`, which `noDash` does not match. `brief.py:1966-1971` composes the dangling clause as a literal | **does not hold**, and the misdiagnosis has consequences, see 3.3 |
| C8 | §1.2, fourteen distinct font sizes in `#view`, with 10px on 5 nodes, 12.5px on 42, 17px on 11; `dashboard.css` has 52 `font-size` declarations and 33 `font:` shorthands | **15** distinct sizes (C omits 18px, one node). Counts measured: 10px **50**, 12.5px **46**, 17px **15**, 11.5px **23**, 13px **39**. `dashboard.css` has **52** `font-size` declarations and **45** `font:` shorthands | **does not hold** on the counts, holds on the shape of the finding |
| C9 | §5.16, 42 interactive elements today, 26 cite chips | `querySelectorAll('button,a,summary,[role=button],input,select')` inside `#view`: **39**. `.cite`: **26** | cite chips hold, interactive count does not |
| C10 | §1.4, 5,992 rendered characters over 242 non-blank lines | **5,995** over **243**, one clock tick apart | **holds** |

C's per-string and per-stylesheet measurements are the most reliable in the
bake-off. C's rendered-geometry and diagnosis claims are the least reliable.

### 2.4 The claims that did not hold, in one place

| designer | claim | measured |
|---|---|---|
| A | `thresholds` carries 28 gates (§3.2) | 21 |
| A | captain lead 0.20, João Pedro 5.57 (§4 wireframe) | 0.16, 5.609 |
| A | 14 ages in five shapes inside the view (§1.3) | 13 in four; the fifth shape is in the shell |
| B | 19 spacing values, 1px to 64px (§1.7) | 18, 1px to 60px |
| B | `squad15` at `brief.py:235`, `_ref` at 98-108 (§5.3) | 934 and 797-807 |
| B | `_ref` needs four more fields (§5.3) | six |
| B | sixteen clock-carrying fields (§6.1) | the list holds 24 |
| C | page is 3,466 px tall (§1.1) | 3,573 |
| C | 5,687 px tall at 446 (§4.2) | 5,897 |
| C | `noDash` causes the broken sentence (§1.5) | the payload arrives broken, `brief.py:1966-1971` composes it |
| C | fourteen font sizes, 10px on 5 nodes (§1.2) | fifteen, 10px on 50 nodes |
| C | 42 interactive elements (§5.16) | 39 |

---

## 3. The failure each designer did not list

### 3.1 Design A: the contract test forbids the key A's ledger reads

`tests/unit/test_web_contract.py:528`,
`test_the_pitch_draws_one_lineup_with_no_toggle_and_no_arrows`, asserts:

```
for gone in ("As picked", "Suggested XI", "db-toggle", "pp-mark", "⇄",
             "suggested_xi", "pitchMode"):
    assert gone not in src
```

The literal string `suggested_xi` is **banned from `home.js` by a passing
test**. A's field map puts `suggested_xi.n_changes`, `.swap_delta_xpts`,
`.captain_delta_xpts` and `.your_captain` on the bench and captain rows
(§3.1), which reintroduces that string. A reads the test in §8.1, quotes its
`read` half, and concludes "the test survives with its docstring rewritten".
A never notices the `gone` half. The ledger as specified fails a test A
listed as surviving.

The same trap catches B, whose entire mark grammar is `suggested_xi.swaps[]`,
`.your_captain`, `.captain_delta_xpts` and `.total_delta_xpts` (§4.1, §5.1).
C escapes it only by proposing to delete the block, which has its own problem
(3.3).

This is not an argument against reading the block. It is an argument that the
test has to be rewritten deliberately, in the same commit, with the reason
stated, because the rule it was protecting (one lineup, no arrows, no
toggle) is still a rule and the string ban was the cheap proxy for it.

**Second A failure, unlisted: `alertRow` disappears.**
`test_the_alert_rows_read_only_fields_the_brief_schema_carries`
(`:119`) calls `_nested_fn_body(src, "alertRow")`, which asserts a function
of that exact name exists. A deletes 1047-1169 and says "`claimFor` survives,
moved into the blockers renderer". A says nothing about `alertRow`, which
lives at 1093-1108 inside the deleted range. The test is not in A's §8.1
table and will fail.

**Third A failure, unlisted: the week where the plan is fresh.** Every
example in A is drawn from the stale payload, where two of four rows say no
answer stands. On a fresh-solver week the transfer row carries the plan's
moves, the captain row carries the plan's captain, and the working block
carries `gain_over_roll`, `objective_mode`, `optimality_gap_pct`, `hits`,
`hit_verdict`, `bank_after_tenths`, `alternatives[]`, `notes[]` and `bounds`.
A's column 3 is 208 px and holds one number per line. A has not shown that
row at its widest.

### 3.2 Design B: the primary object does not exist until the backend changes

B's own §1.8 is the finding, and B's §11.1 names the exposure honestly. The
part B does not follow through is what the intermediate state costs.

Measured: the brief names 5 of 15. B's fallback (§8, `squad_overview`
unreachable) draws ten integer slots. B calls this "the design's worst
state". It is worse than that, because it is also the **only** state until
§5.3 ships. Between the day the page lands and the day the `squad` array
lands, every card on the page is joined across two panels at render time,
which is the cross-panel join the repo's fixture-adapter bug class exists to
warn about (`test_the_fixture_adapter_reads_only_fields_fixture_board_publishes`
at `:157` is that scar tissue).

**The failure B did not list: the diacritics.** `squad_overview` serves
`Ødegaard` and `João Pedro`. B's wireframes print `Odegaard` and
`Joao Pedro`, and B's card geometry (§9.4) sizes `--pp-w` at 104px against
`Calvert-Lewin`. Rule 8's spirit is that a surface repeats what the payload
says, verbatim. A card that transliterates a served name has renamed a fact
without saying so.

**The second failure B did not list: B has no test section at all.** B
deletes `tileEl`, `tileText`, `alertRow`, `claimFor`, `captainBlock`,
`unconstrainedLine` and `renderSolver` and names zero of the seven tests that
pin them by name. Section 6 of this review does that work.

### 3.3 Design C: the fix does not fix the defect, and the deletion removes a guard

C's §1.5 is the clearest-written finding in the bake-off and its mechanism is
wrong. The served string is:

```
transfer plan generated 2026-09-08 for GW4–8; a deadline has passed since ,  its moves were priced against a squad you no longer have.
```

Zero U+2014. The only dash is U+2013 in `GW4–8`. `noDash` matches
`/\s+\u2014\s+/` and `/\u2014/` as escapes, so it does nothing to this string.
The dangling clause is composed as a literal at `brief.py:1966-1971`, almost
certainly by pasting `normalize_prose` output back into the source.

Two consequences C did not reach:

1. **C's step 1 does not remove the defect it is aimed at** unless the actual
   literal at 1966-1971 is rewritten. C's build order calls it "one line" and
   it is, but it is a different line from the one C diagnoses.
2. **Deleting `noDash` (C 5.14) removes the last guard on third-party
   prose.** `alerts[].news` is FPL's own copy, printed verbatim by contract.
   `intel` items are model-authored. Rule 4 forbids em dashes in any UI
   string, and `noDash` is the only thing between a supplier's em dash and
   the page. `test_no_em_dashes_in_dashboard_strings` (`:613`) scans the
   **source** for literals and would not catch a runtime one.

**The failure C did not list: `suggested_xi` is a required key with tests.**
C §3.3 proposes to stop serving it. `RESULT` in `brief.py:710-718` lists
`suggested_xi` in `required` and sets `additionalProperties: False`, and
`tests/unit/test_dashboard_brief.py` asserts on it at lines 281, 307, 340 and
1419. Removing it is a breaking contract change, not a drop, and the brief is
not the dashboard's private payload. Rule 9 is a rule about the UI reading
the payload, and it does not license the UI to shrink the payload.

**The second failure C did not list: deleting the tiles breaks two tests, one
of them a threshold rule.** `test_the_tiles_read_only_fields_the_brief_schema_carries`
(`:139`) requires functions named `tileEl` and `tileText`.
`test_the_dashboard_hardcodes_no_gate_thresholds` (`:317`) asserts
`"t.gate" in src`, so if the tiles leave `home.js` the threshold-echo rule
loses its only enforcement on this page.

---

## 4. The owner's weekly loop, run against each design

The owner reads on a laptop, sometimes a phone, wants transfer, captain,
bench, chip in that order with the gaps that block them, and the win is a
faster decision with its evidence visible.

### 4.1 squad_source is public picks six days old

This is the live state. `squad_source.live` is false, `picks_gw` is 4, the
read is 5 days old, and every captain and bench number was computed on a
fifteen that may already be wrong.

- **A** puts it in the header line above all four rows and forces `stale` on
  every row citing `squad_overview`. Strongest handling of the three, because
  the fact arrives before the answers it qualifies.
- **B** puts it in the top gaps row and a persistent band, then draws fifteen
  convincing cards under it. B names this itself: "a drawn squad is more
  convincing than a list, so this design makes that error more dangerous".
  Correct and not resolved.
- **C** puts it in region 4, **below** all four answers, and §9.2 admits the
  mitigation is weak. On a phone that is one full thumb-scroll after the
  captain pick. C's three-placement analysis is honest and picks the wrong
  one.

### 4.2 The solver is stale

All three handle this well, because `brief.py:384-387` already serves no plan
body when stale and all three keep that. A gives it a blockers sub-row with
`[Re-solve]`, B an outlined tag, C a region 3 row. A and C both keep
`Re-run solve` out of the refresh control's region, which matters.

B's outline rule is the weak one, and B says so in §11.2: on today's payload
four of five input clocks predate the last deadline, so **every** mark would
be an outline and the distinction carries no information on the day it
matters most.

### 4.3 Two decisions conflict: transfer out the captain

Only **B** has a rule. §4.2 sets `transfer > captain > bench > chip` and
demotes the loser to a counted square with its own drawer entry. B flags it
as untested, which is accurate: today's payload has no collision.

**A cannot express it.** The ledger is one row per question, so a player who
is both the sell leg and the armband appears on row 1 and row 2 with nothing
joining them. A reader who acts on row 1 has silently invalidated row 2, and
the ledger's precedence (`verdict.precedence`) adjudicates between voices for
one question, never between questions.

**C cannot express it either**, and C's rows are one line each, so there is
less room to notice.

This is the strongest single argument for the squad surviving somewhere on
the page: the fifteen are the only object on which two decisions about the
same player land in the same place.

### 4.4 A gap blocks an answer

`watch_log` reports 4 gaps today (`owned_price_flow`, `price_targets`,
`creator_shift`, `solver`), plus one `empty_kinds` entry and two alerts.

- **A** promotes them to a BLOCKERS ledger row with a count, capped at four
  sub-rows, each with a named fix. Best of the three.
- **C** caps region 3 at three rows and pushes `empty_kinds` to a footer.
  Today that drops one of the four to a `+N more` link.
- **B** puts them first, uncapped, and §3.4's height budget has zero slack,
  so a fourth gap row pushes the bench tray under the fold. B calls that "the
  correct failure" and it is defensible.

### 4.5 On a phone

Measured floor, at 446 and not reachable below it: 5,897 px tall, verdict at
1,685.

- **A** collapses five columns to two lines and keeps the right-aligned
  numeric edge. A's §11.6 names the risk that a wrapping number line breaks
  the decimal edge, which is the density argument. Honest, and the layout
  still puts four answers plus a blocker above 844.
- **B** abandons the pitch below 640px for fifteen strips, which is B's own
  §11.6: "the narrow form is better at everything except being the thing this
  design argues for".
- **C** stacks four regions in the same order and reaches the fold at region
  4, which is the squad-source warning. On a phone C's ordering defect is at
  its worst.

---

## 5. Rule 9 audit: elements not in the served payload

Rule 9 says a panel that does not serve a fact means the page does not print
it, and absence is shown as absence with the reason.

| design | element | status |
|---|---|---|
| A | `verdict.lines[].confidence` and `.confidence_field` (§10.2) | **not served.** A proposes serving it and keeps a browser-side derivation as fallback. The fallback is a threshold lattice computed in the view, which is the second implementation the repo's own contract warns against. Additive if served, rule-9 violating if only derived |
| A | `sources_refresh` map (§10.1) | **not served.** Additive, legitimate, and A is right that without it the view would have to guess which task writes which panel |
| A | `squad_source.fix_kind` (§10.3) | **not served.** Additive, legitimate, removes a shape inference |
| A | the four confidence words `firm`, `contested`, `stale`, `none` | vocabulary invented by the view. A admits this in §11.3 |
| A | "4 things stop this page answering" | a count over served rows, acceptable |
| B | `squad` array (§5.3) | **not served.** See below |
| B | the tie line with its delta | drawn from `suggested_xi.swaps[].numbers.swing` and `captain_delta_xpts`, both served |
| B | tag words `SELL`, `BUY`, `START`, `SIT`, `C`, `your C`, `TC`, `BB` | view templates keyed by served `rule` and served codes. Same discipline the verdict row already uses. Acceptable |
| B | collision precedence `transfer > captain > bench > chip` | invented in the view. Not served, and not derivable from the payload. It is a UI ordering rule rather than a fact, which is the least bad category, but it should be stated in the code as an authored constant, not presented as payload-led |
| C | nothing added | C introduces no field. C instead proposes **removing** `suggested_xi`, `season`, `entry_id`, `projection_gw` and 17 thresholds from the payload, which is a breaking change to a schema with `additionalProperties: False` and four tests |

**No design asks for a free-text recommendation field.** All three explicitly
refuse one, and all three delete `/api/briefing` from the front page for that
reason. That much is unanimous and correct.

### 5.1 The judgement on B's `squad` array

**It is a legitimate additive contract change, and design B only works after
it lands.** Both halves are true and they are not in tension.

Legitimate, on the evidence:

- The data is already in memory. `brief.py:934` builds
  `squad15 = starters + bench` from `squad_overview`'s own rows, which the
  brief has already read and already depends on.
- The serialiser exists. `_ref()` at `brief.py:797-807` emits code, name,
  pos, team, team_code, price, own_pct. Six further fields (xpts, p_haul,
  status, news, is_captain, is_starter) are present on the same rows.
- No new query, no new join, no second implementation of any metric. The
  numbers would be the same numbers `squad_overview` serves.
- It removes a cross-panel join from the page's primary object, which makes
  the payload more self-describing rather than less.

Only-works-after, on the evidence:

- The brief names 5 of 15 today. Measured, not asserted.
- `RESULT` sets `additionalProperties: False`, so the key needs a schema
  property and a `required` decision before anything can read it. This is one
  edit in `brief.py`, in the `RESULT` properties block at lines 710-760, plus
  population beside where `squad15` is built.
- Until then B's page draws ten integers. B's own abandonment test says the
  design does not ship in that state, which is the right call.

The array is worth having whichever design wins, because A's captain and
bench working blocks and C's lineup both currently join `best_xi.xi_codes`
against `squad_overview.starters` in the browser.

---

## 6. Verdict

**Build design A's ledger as the page, keep design C's disposition of the
lineup, and land design B's `squad` array as the one contract change.**

Three lines:

1. The answer goes above the fold as four ledger rows in the owner's stated
   order, because the payload already computes exactly that block, in exactly
   that order, with a served precedence, and no design improves on believing
   it.
2. The pitch does not get deleted. It moves below the fold, because it is the
   only surface where a bench inversion can be checked and the only object on
   which a transfer and a captaincy can collide visibly.
3. B's squad-first framing loses on dependency and on the phone, and C's
   ordering loses by putting the squad-source warning below the answers it
   qualifies, but both contribute sections that A got wrong.

### 6.1 The synthesis, by numbered section

| take | from | why |
|---|---|---|
| §0, §2 (the row, reading order, confidence lattice, working block) | **A** | The ledger row is the only proposal with a fixed numeric column and a fixed meta column, which is the alignment complaint answered with geometry rather than taste |
| §3 (field map, above-fold / below-fold / dropped) | **A**, corrected for 21 thresholds | A's is the only map that accounts for every key in both directions |
| §6 (refresh: three verbs, re-read reports its outcome, `fmtAgeDays`) | **A** | A is the only design that distinguishes re-read from re-run from re-solve and makes a re-read report that nothing moved. C's single Refresh is right about cost and silent about outcome |
| §7 (empty, stale and error states) | **A** | Eleven named states against C's ten and B's thirty-one; A's are the ones keyed to `verdict.lines[].rule` |
| §9 (five type tokens, five spacing steps, column tokens) | **A** | Every token names a size `app.css` already uses. C's two-size scale forces `.card` padding from 14px to 16px on a shared component, which is a change to Fixtures and Pipelines paid for by the dashboard |
| §3.2 disposition of the lineup: below the fold, working behind the bench row | **C** | This is the single correction A needs. A §11.1 names deleting the pitch as the thing that would make the design fail, and C shows it does not have to be deleted to get the answer above the fold |
| §5 (the deletion list as an ordered list with measured pixel cost per block) | **C** | C's arithmetic is sound (880 lines, verified by summing its own table) and its ordering puts reversible one-line wins first |
| §10 steps 1 and 2 (fix the `brief.py` reason literal, reorder `host.append`) | **C** | Both are one commit each, both are worth having whichever design wins, and step 2 alone moves the answer 562 px up the page |
| §4.2 (collision precedence when one player attracts two decisions) | **B** | The only proposal that handles transfer-out-the-captain. Applied to the ledger it becomes a cross-reference on the affected rows rather than a tag on a card |
| §5.3 (the additive `squad` array) | **B** | Removes a browser-side cross-panel join from A's working blocks and from the lineup |
| §7 (the drawer opens from the player, blocks independently nullable, every null carries a reason) | **B** | The lineup below the fold needs a working layer, and `attachPlayerDrawer` already exists at `home.js:192` |

### 6.2 What the build must change from design A before it starts

1. **Do not delete `home.js:1170-1522`.** A's §8 deletes the pitch and A's
   §11.1 says that is the design's abandonment risk. The pitch moves below
   the fold, unchanged in substance, minus `pjClass` and `tierWord` per C
   5.11. This is the largest single departure from A as written.
2. **Decide the `suggested_xi` string ban deliberately.** Either rewrite
   `test_the_pitch_draws_one_lineup_with_no_toggle_and_no_arrows` to ban
   `pitchMode`, `db-toggle`, `pp-mark` and `Suggested XI` while allowing
   `suggested_xi` field reads, or read the block through an alias. Rewriting
   the test is the honest option and the commit message says why the proxy
   changed.
3. **`thresholds` is 21, not 28.** A's field map promises to place 28 gates.
4. **Fix the captain figures.** `captain_lead_xpts` is 0.16, the second
   candidate is 5.609, and the served name is `João Pedro`. Print served
   names with their diacritics.
5. **Keep `alertRow` by name**, or update `:119` in the same commit.
6. **Keep `noDash`.** A does not propose deleting it; C does. It stays as the
   runtime guard on `alerts[].news`.

### 6.3 The single biggest thing the build must get right

**The stale-plan guard has to survive being moved.**
`test_a_stale_or_missing_plan_renders_no_plan_content` (`:551`) pins a
regex against a function literally named `renderSolver`:

```
const plan = (S && (S.state === "fresh" || S.state === "aging"))
```

and then asserts no `plan.captain`, `plan.moves`, `plan.alternatives` or
`plan.gain_over_roll` renders anywhere outside that function's body. Today
`solve.state` is `stale` and `solve.age_hours` is 240.6. That guard is the
only thing stopping a 10-day-old plan's moves from printing as this week's
advice. Every design moves the code it is attached to, and A correctly calls
it "the most valuable test on the page".

The guard moves with the plan body into the transfer row's working block. The
test is retargeted at the new function in the same commit, the regex is
copied character for character, and the `outside` leak check keeps scanning
the whole file. If that assertion weakens, the rebuild has shipped the exact
failure the owner would least forgive: a confident transfer instruction
priced against a squad he no longer holds.

---

## 7. Build order

### 7.1 Land first, reversible, one commit each

| # | change | file | effect |
|---|---|---|---|
| 1 | Rewrite the `solve.reason` literal at `brief.py:1966-1971` so the clause after "a deadline has passed since" is a whole sentence, and keep `GW4–{n}` out of `normalize_prose`'s path (that helper rewrites U+2013 to a comma and would print `GW4, 8`) | `fpl_edge/platform/scripts/brief.py` | removes a broken sentence that renders 3 times, measured |
| 2 | Reorder `host.append` at `home.js:222-224` so `verdictCard` precedes `gapStrip` and `standingStrip` | `web/dist/js/views/home.js` | moves the verdict from y=806 to roughly y=244 before any deletion |

### 7.2 The reconciled deletion list

Ranges read out of `home.js` for this review, not copied from a design. The
three documents disagree by a line or two at several boundaries; these are the
section-comment boundaries in the file.

| order | lines | count | block | fate | A | B | C |
|---|---|---:|---|---|---|---|---|
| 1 | 2163-2231 | 69 | `renderStanding` | move to Planner | below fold | Season tab | delete |
| 2 | 810-1045 | 236 | the agent briefing, including `briefingDupes` at 882-916 | delete from this page, `/api/briefing` and Chat keep it | own tab | delete | delete |
| 3 | 1047-1168 | 122 | `claimFor` 1048-1092, `alertRow` 1093-1108, `watchTileRow` 1109-1130, `renderWatchStrip` 1131-1168 | **`claimFor` and `alertRow` survive by name**, relocated into the blockers row. `watchTileRow` and `renderWatchStrip` go | partial | delete | partial |
| 4 | 1523-1625 | 103 | `moveFace` 1525, `gwsText`, `moveSentence`, `moveEl`, `renderMoves` | becomes the transfer row's working block. `moveFace` and `gwsText` survive | working block | tags | delete |
| 5 | 353-416 | 64 | topbar stat tiles | bank and free transfers to the transfer row, chip to the chip row, XI sum and median to the bench working block, GW to the header line | delete | delete | reduce |
| 6 | 418-448 | 31 | chip-status strip | into the chip row's working block | delete | delete | keep below |
| 7 | 450-464 | 15 | provenance banner | delete, `squad_source` renders once | delete | delete | delete |
| 8 | 1321-1379 | 59 | `captainBlock` | becomes the captain row's working-block table | rebuild | drawer | delete |
| 9 | 1176-1185, 1193-1201 | 19 | `pjClass`, `tierWord` | delete, the xPts number keeps its value and loses its tint | delete | keep | delete |
| 10 | 1510-1518 | 9 | the two legend paragraphs | delete, badge meanings live in `title` | delete | delete | delete |
| 11 | 320-329 | 10 | `pulsePitch` | replace with a plain anchor to the lineup | keep | keep | replace |
| 12 | 164-175, 236-243 | 20 | `skeleton` and its eight calls | replace with one line naming the calls in flight | keep | keep | replace |
| 13 | 466-808 | 343 | the verdict | **rewritten in place as five ledger rows.** The survivor | rewrite | replace | keep |
| 14 | 1170-1522 | 353 | the pitch | **kept, moved below the fold**, minus 9 and 10 | delete | primary | keep |
| 15 | 1626-1943 | 318 | the solver card | plan body becomes the transfer row's working block, carrying its `fresh`/`aging` guard verbatim. `rerunButton`, `startSolvePolling`, `solveRunningEl`, `lastLogLine`, `unconstrainedLine` and `fcName` survive | working block | gaps row | keep below |
| 16 | 1944-2066 | 123 | `tileText`, `tileEl`, `renderTiles` | **kept below the fold as SIGNALS**, minus the two watch kinds | below fold | Signals tab | delete |
| 17 | 2067-2132 | 66 | `renderWatch` | kept below the fold | below fold | Checks tab | delete |
| 18 | 2133-2162 | 30 | `gapRow`, `cmdFix` | rebuilt on the ledger grid as the blockers row | promote | keep | keep |
| 19 | 2232-2331 | 100 | `renderGaps` | promoted to the blockers row | promote | keep | keep |
| 20 | 2332-2344 | 13 | provenance foot | unchanged | keep | delete | keep |

Net deletion is roughly 465 lines rather than A's 1,144 or C's 880, because
the pitch (353) and the tiles (123) and the watch log (66) stay. That is the
cost of keeping the evidence, and it buys back A's §11.1 risk and C's §9.1
risk in one move.

### 7.3 Payload contract changes, additive only

| # | change | serialiser | schema |
|---|---|---|---|
| 1 | `squad`: array of `{code, name, pos, team, team_code, price, own_pct, xpts, p_haul, status, news, is_captain, is_starter}` | populate from `squad15` at `brief.py:934`, through `_ref()` at `brief.py:797-807` plus the six extra fields off the same rows | new `_SQUAD` item schema beside `_SQPROJ`, registered in `RESULT["properties"]` at `brief.py:710-760`. **Not** added to `required`, so an older view and an older test both keep passing |
| 2 | `sources_refresh`: `{panel_name: registry_task_id \| null}` | a map built from `fpl_edge/pipelines/registry.py` at the point the brief records which panels it called | `{"type": "object", "additionalProperties": {"type": ["string", "null"]}}`, optional |
| 3 | `squad_source.fix_kind`: `"account_paste" \| "command"` | beside where `squad_source.fix` is set | added to `_SQUAD_SOURCE` properties, optional |

Not built: A's `verdict.lines[].confidence`. The four confidence words are a
view vocabulary over served fields and the deciding field prints beside the
word, so the reader can check it. Serving a word the view could not
independently justify would move a UI label into the payload, which is the
wrong direction. If the lattice proves load-bearing after two gameweeks, it
can be served then.

**Nothing is removed from the payload.** C's §3.3 proposal to drop
`suggested_xi`, `season`, `entry_id`, `projection_gw` and 17 thresholds is
rejected: `additionalProperties: False` plus `required` plus four assertions
in `tests/unit/test_dashboard_brief.py` make it a breaking change, and the
brief serves more than one consumer.

### 7.4 Tests that break, and which are real

Baseline: `tests/unit/test_web_contract.py`, 61 tests, `REAL_PYTEST_EXIT=0`.
Fifteen of them read `VIEWS["home"]`.

**Expected contract updates.** The test pins structure the rebuild
deliberately moves. Update in the same commit as the code, with the docstring
saying what replaced it.

| test | line | why it breaks | update |
|---|---:|---|---|
| `test_the_pitch_draws_one_lineup_with_no_toggle_and_no_arrows` | 528 | bans the literal `suggested_xi`, which the bench and captain rows now read | keep the `pitchMode` / `db-toggle` / `pp-mark` / `Suggested XI` bans, drop the `suggested_xi` string ban, keep all five `read` assertions |
| `test_an_outdated_briefing_folds_and_never_renders_items_as_current` | 604 | `renderIntel` leaves `home.js` | move to a briefing-view test, unchanged in substance |
| `test_the_gaps_strip_is_payload_derived_and_names_every_fix` | 587 | `renderGaps` becomes the blockers row; `intelOutdated()` and `generateBtn("Regenerate")` leave with the briefing | retarget at the blockers renderer, drop those two reads, **keep** `"myteam auth" not in src` and `squadSource.fix` and the hidden-when-empty assertion |
| `test_the_pitch_fallback_is_the_clubmark_discipline` | 294 | only if the pitch's `.pp-face` sizing changes. Under this synthesis the pitch survives, so this should **not** break | no change expected, verify |
| `test_the_alert_rows_read_only_fields_the_brief_schema_carries` | 119 | `alertRow` and `claimFor` relocate | keep both function names, retarget `_nested_fn_body` only if renamed |

**Real regressions if they break.** These pin a rule, not a shape. If one
fails, the change that did it is reverted before anything else happens.

| test | line | the rule |
|---|---:|---|
| `test_a_stale_or_missing_plan_renders_no_plan_content` | 551 | a stale plan renders no plan content. See 6.3. The `fresh`/`aging` guard regex is copied character for character into the new function and the `outside` leak scan keeps covering the whole file |
| `test_the_dashboard_hardcodes_no_gate_thresholds` | 317 | gates come from the payload. Requires `t.gate` to survive, which it does because the tiles stay |
| `test_no_em_dashes_in_dashboard_strings` | 613 | rule 4 on this page |
| `test_haul_odds_are_gated_on_the_simulation_date` | 568 | a haul number from a 29-day-old simulation never renders. Requires the literal `"haul odds unavailable (last simulation"` to survive |
| `test_the_solver_objective_is_never_relabelled_as_xpts` | 208 | requires `gain_over_roll`, `objective_mode` and `"solver forecast"` to stay in `home.js`, and `rank_mv` to stay out. `fcName` at `home.js:182` must survive the solver-card rewrite |
| `test_the_tiles_read_only_fields_the_brief_schema_carries` | 139 | requires `tileEl` and `tileText` by name, and `throw new Error` inside `tileEl` |
| `test_the_best_xi_and_squad_source_read_only_schema_fields` | 540 | `bestXi.` and `squadSource.` read only schema fields |
| `test_the_pitch_reads_only_fields_the_squad_schema_carries` | 94 | requires `function pcard` and its field subset |
| `test_the_dashboard_names_the_unconstrained_best_beside_the_headline` | 645 | requires `"if hits were free"` and `href = "#planner"` in `home.js`. **B and C both delete `unconstrainedLine` and neither names this test.** The line survives in the transfer row's working block |
| `test_every_panel_script_is_rendered_by_some_view` | 65 | every declared panel is called by some view. Safe as long as `tryPanel("squad_overview")` and `tryPanel("dashboard_brief")` both survive, and no panel loses its only caller when tiles or the watch log move |

Outside `test_web_contract.py`: `tests/unit/test_dashboard_brief.py` at 281,
307, 340 and 1419 asserts on `suggested_xi`. Under this synthesis none of them
change, because nothing is removed from the payload. Adding the optional
`squad` property should leave them green; if 281 enumerates top-level schema
properties, it takes one additive edit.

### 7.5 Acceptance checks the orchestrator runs

Run all six. Record the number, not a pass word.

**1. Viewport, 1280x800.** With the browser emulating 1280x800 on
`http://localhost:8321/#home`:

```
const v = document.querySelector('#view');
const rows = [...v.children].map(c => {
  const r = c.getBoundingClientRect();
  return {cls: c.className, top: Math.round(r.top+scrollY), h: Math.round(r.height)};
});
```

| measure | before | required after |
|---|---:|---|
| `document.documentElement.scrollHeight` | 3,573 | under 2,600 |
| top of the first decision row | 806 | under 300 |
| bottom of the fourth decision row | 1,189 | **at or under 800** |
| `#view.children.length` (region count) | 14 | **at or under 8**, with at most 4 above y=800 |
| `document.documentElement.scrollWidth > innerWidth` | false | false |

The fourth-row bottom is the acceptance criterion that matters. Transfer,
captain, bench and chip all visible without scrolling at 1280x800.

**2. Viewport, 390 wide.** The harness clamps `innerWidth` to **446**; a true
390 cannot be measured here and any figure quoted at 390 is a floor. Measure
at 446 after a fresh navigation, not after a resize, because a resize without
a remount under-reports by about 500 px (measured: 5,367 on resize against
5,897 on fresh mount).

| measure | before, at 446 | required after |
|---|---:|---|
| `document.documentElement.scrollHeight` | 5,897 | under 4,200 |
| top of the first decision row | 1,685 | under 400 |
| bottom of the fourth decision row | 2,311 | under 1,100 |
| horizontal overflow | false | false |

**3. The age-format grep.** One vocabulary, days, with the exact instant in
`title`.

```
grep -c "ageText(\|fmtSpan(" web/dist/js/views/home.js        # 13 today, expect 0
grep -c "clockText(" web/dist/js/views/home.js                # 10 today, expect at most 2
grep -n "fmtAgeDays" web/dist/js/app.js                       # expect 1 definition
```

And over the rendered page at 1280x800:

```
(v.innerText.match(/\b\d+d(\s\d+h)?\b/g) || []).length   // 13 today, expect 0
(v.innerText.match(/\b\d{2}:\d{2}Z\b/g) || []).length    // 11 today, expect at most 2
(v.innerText.match(/\b\d+(\.\d+)?h\b/g) || []).length    // expect 0 outside the deadline countdown
```

The deadline countdown keeps `fmtSpan` in the shell, which is forward-looking
and not an age. `solve.age_hours` of 240.6 prints as `10 days`.

**4. Duplicate strings.** Over `#view`'s `innerText`, substring counts:

| string | before | required after |
|---|---:|---:|
| `public picks` | 4 | 1 |
| `its moves were priced against a squad you no longer have` | 3 | 1 |
| `best XI by consensus xPts` | 3 | at most 2 |
| `haul odds` | 3 | at most 2 |
| `close call` | 2 | 1 |
| `Re-run solve` | 2 | 1 |
| `a deadline has passed since ,` | 3 | **0**, the string is rewritten in `brief.py` |

**5. The suite gate.** Per rule 2 and rule 3:

```
uv run pytest -q > out.txt 2>&1; echo "REAL_PYTEST_EXIT=$?" >> out.txt
grep "^FAILED" out.txt | sort > after.txt
diff baseline.txt after.txt
```

The 11 pre-existing `tests/audit/` failures may shrink. They may not grow and
their membership may not change. `tests/unit/test_web_contract.py` was green
at exit 0 before this work, so any failure there is attributable and must be
either an update listed in 7.4 or a revert.

**6. The prose gate**, on every authored UI string and on the commit message:

```
uv run python -c "from fpl_edge.platform import prose_style as ps; \
  t=open('web/dist/js/views/home.js').read(); print(t.count(chr(8212)))"
```

Zero, and `test_no_em_dashes_in_dashboard_strings` green.

---

## 8. What each design contributed that the others missed

- **A** measured the page correctly. Its region table, its narrow-viewport
  numbers and its test line numbers all reproduce exactly, which is why its
  deletion ranges are the ones this build order uses.
- **B** found the fact that decides the shape of the page: the brief names 5
  of the 15 players it computes over. That single measurement is what turns
  "draw the squad" from a layout preference into a contract question, and it
  is the reason the `squad` array is in the build order.
- **C** counted the repetition. Eleven duplicate-string counts, all exact, and
  eighteen spacing values, exact. The page prints `public picks` four times
  and `18:17Z` eight times, and no amount of restructuring fixes that without
  someone having counted it first.
