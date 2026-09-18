"""The frontend's assumptions, checked against the backend's declarations.

Four shipped bugs prove what a missing frontend test costs: a renderer reading
a key the panel's own schema forbids ("No data." over real data); player cards
reading `price_tenths` where the schema says `price` (fifteen cards of £NaN);
an outcome vocabulary the panel never emits; and a hardcoded deadline. These
are CONTRACT tests on exactly that seam — the JS held against the registered
panels and schemas.

The UI is zero-build ES modules (DESIGN.md §2.2): `index.html` is a shell,
`js/app.js` the shared layer, one file per view in `js/views/`. The tests scan
those sources directly; there is no bundle to parse.
"""

from __future__ import annotations

import re
from pathlib import Path

from fpl_edge.platform import panels as panels_mod
import fpl_edge.platform.scripts  # noqa: F401 - registration is the import
from fpl_edge.platform.registry import script as get_script

WEB = Path(__file__).resolve().parents[2] / "web" / "dist"
APP = (WEB / "js" / "app.js").read_text()
HTML = (WEB / "index.html").read_text()
VIEWS = {p.stem: p.read_text() for p in sorted((WEB / "js" / "views").glob("*.js"))}
#: Shared components render panels too. `chatter.js` is mounted from the
#: xPoints and Template drawers rather than being a view of its own, and
#: scanning only `views/` reported its panel as unrendered -- the test's model
#: of the app, not a real gap. A renderer is a renderer wherever it lives.
COMPONENTS = {p.stem: p.read_text()
              for p in sorted((WEB / "js" / "components").glob("*.js"))}
ALL_JS = APP + "".join(VIEWS.values()) + "".join(COMPONENTS.values())


def _schema_props(script: str, *path: str) -> set[str]:
    node = get_script(script).result_schema
    if "oneOf" in node:                     # registration wraps in oneOf[real, EMPTY]
        node = node["oneOf"][0]
    for key in path:
        node = node["properties"][key]
        if node.get("type") == "array":
            node = node["items"]
    return set(node.get("properties", {}))


def _strip_comments(src: str) -> str:
    """Comments are prose, not field reads.

    A comment that names the field it is explaining ("there is deliberately no
    `o.market_age_hours` fallback") would otherwise be scanned as a read of it,
    and the check would fail on the documentation of its own rule.
    """
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def _fn_body(src: str, name: str) -> str:
    m = re.search(rf"function {name}\([^)]*\)\s*\{{(.*?)\n\}}", src, re.S)
    assert m, f"function {name} not found"
    return m.group(1)


def test_every_panel_script_is_rendered_by_some_view() -> None:
    """A registered panel nobody calls is dead weight wearing a schema; a view
    calling an unregistered script errors at runtime. Both directions pinned."""
    called = set(re.findall(r'runPanel\(\s*"(\w+)"', ALL_JS))
    called |= set(re.findall(r'panelInto\(\w+,\s*"(\w+)"', ALL_JS))
    # A view may wrap runPanel to add fallback or 404-memoisation and still be
    # the thing that renders a panel. `tryPanel` is the fixtures view's wrapper
    # -- it asks for the split board and falls back to the legacy ticker -- and
    # scanning only the bare call reported the ticker as rendered by nobody.
    # That was the test's model of the app being behind the app, not a defect.
    called |= set(re.findall(r'tryPanel\(\s*"(\w+)"', ALL_JS))
    declared = {p.script for p in panels_mod.PANELS}
    unrendered = declared - called
    assert not unrendered, f"panels with no view rendering them: {sorted(unrendered)}"
    unknown = called - declared
    assert not unknown, f"views call scripts no panel declares: {sorted(unknown)}"


def test_the_shell_is_a_shell_and_views_are_modules() -> None:
    """index.html regrowing inline logic is the failure §2.2 exists to prevent."""
    inline = re.search(r'<script type="module">(.*?)</script>', HTML, re.S)
    assert inline, "the shell must load the app as a module"
    body = inline.group(1)
    assert len(body) < 1500, "the shell's inline script is growing logic again"
    assert "register(" in body and "start()" in body
    for name, src in VIEWS.items():
        assert re.search(r"export default", src), f"view {name} has no default export"


def test_the_pitch_reads_only_fields_the_squad_schema_carries() -> None:
    """`p.price_tenths` rendered £NaN on all fifteen cards; the schema says
    `price` and forbids everything else."""
    allowed = _schema_props("squad_overview", "starters")
    body = _fn_body(VIEWS["home"], "pcard")
    used = set(re.findall(r"\bp\.(\w+)", body))
    unknown = used - allowed
    assert not unknown, (
        f"pcard reads {sorted(unknown)}, which the squad player schema does "
        f"not carry (it has {sorted(allowed)}); those render as undefined"
    )


def _nested_fn_body(src: str, name: str) -> str:
    """A function defined INSIDE the view's default export (two-space indent).

    ``_fn_body``'s first-``\\n}`` heuristic would run to the end of the file
    for these and scan unrelated code into the subset check.
    """
    m = re.search(rf"^  function {name}\([^)]*\)\s*\{{(.*?)\n  \}}",
                  src, re.DOTALL | re.MULTILINE)
    assert m, f"nested function {name} not found"
    return m.group(1)


def test_the_alert_rows_read_only_fields_the_brief_schema_carries() -> None:
    """The dashboard's alert templates are its flatten(): the one place the
    view meets dashboard_brief's alert contract. A field read that the schema
    does not carry silently renders undefined — the fixtures-adapter bug
    class, pinned here for the rebuilt front page."""
    allowed = _schema_props("dashboard_brief", "alerts")
    src = _strip_comments(VIEWS["home"])
    body = (_nested_fn_body(src, "alertRow")
            + _nested_fn_body(src, "claimFor"))
    used = set(re.findall(r"\ba\.(\w+)", body))
    unknown = used - allowed
    assert not unknown, (
        f"the alert renderer reads {sorted(unknown)}, which the brief's "
        f"alert schema does not carry (it has {sorted(allowed)})"
    )
    # decision-typing and sourcing are the row's anatomy
    for field in ("kind", "numbers", "source_panel"):
        assert field in used, f"alert rows must render a.{field}"


def test_the_tiles_read_only_fields_the_brief_schema_carries() -> None:
    allowed = _schema_props("dashboard_brief", "tiles")
    src = _strip_comments(VIEWS["home"])
    body = (_nested_fn_body(src, "tileEl")
            + _nested_fn_body(src, "tileText"))
    used = set(re.findall(r"\bt\.(\w+)", body))
    unknown = used - allowed
    assert not unknown, (
        f"the tile renderer reads {sorted(unknown)}, which the brief's tile "
        f"schema does not carry (it has {sorted(allowed)})"
    )
    for field in ("number", "gate", "source_panel"):
        assert field in used, f"tiles must render t.{field}"
    # the required-args contract: a tile missing a leg throws in the renderer
    # rather than rendering a number without its source
    assert "throw new Error" in _nested_fn_body(src, "tileEl")


def test_the_fixture_adapter_reads_only_fields_fixture_board_publishes() -> None:
    """The whole fixtures tab rendered blank on a name mismatch, silently.

    The view read `o.attack_xg`; the panel publishes `opponent_only.attack_xg`.
    Every lookup returned undefined, so the grid drew 20 rows of empty club
    names, greyed every cell "no fit", and printed "The split is not in this
    payload" -- while holding the split. Nothing failed. Nothing threw. Every
    message on screen was individually true.

    `flatten()` is now the single place the two contracts meet, so it is the
    single place worth pinning: everything it reads off an opponent must be a
    field `fixture_board` actually declares.
    """
    # The legacy fixture_ticker fallback is deleted, so fixture_board's
    # opponent schema is the ONLY shape the view may legitimately see.
    # Anything outside it is a field no registered panel publishes.
    allowed = _schema_props("fixture_board", "teams", "fixtures", "opponents")
    body = _strip_comments(_fn_body(VIEWS["fixtures"], "flatten"))
    used = set(re.findall(r"\bo\.(\w+)", body))
    unknown = used - allowed
    assert not unknown, (
        f"flatten() reads {sorted(unknown)} off an opponent, which the "
        f"fixture_board opponent schema does not carry (it has "
        f"{sorted(allowed)}); those silently become undefined"
    )
    # The nested blocks are the whole reason this adapter exists.
    for nested in ("opponent_only", "fixture_specific", "market"):
        assert nested in allowed and f"o.{nested}" in body, (
            f"flatten() must read o.{nested}; that nesting is what the view "
            "got wrong the first time"
        )


def test_the_fixture_grid_reads_the_split_the_panel_actually_names() -> None:
    """`anySplit` decides whether the page believes it has two axes.

    It was false for a live payload carrying both, which is how the page came
    to deny the split in its own banner. Pin the two field names that decision
    rests on.
    """
    opp = _schema_props("fixture_board", "teams", "fixtures", "opponents",
                        "opponent_only")
    assert {"attack_ease", "defence_ease"} <= opp, (
        "fixture_board must publish both axes under opponent_only; the view "
        "keys its entire split/no-split decision on them"
    )
    body = _fn_body(VIEWS["fixtures"], "flatten")
    for field in ("attack_ease", "defence_ease"):
        assert field in body, f"flatten() must surface {field}"


def test_the_solver_objective_is_never_relabelled_as_xpts() -> None:
    """The no-silent-blend rule (FINAL_SPEC Kill 3), updated for the
    transfer-plan solver card: the dashboard now renders the plan's own
    `gain_over_roll`, but only under the house label ("solver forecast")
    with the currency named by the payload's `objective_mode` — never
    blended with, or presented as, the consensus xPts on the pitch. So: no
    hardcoded unit, the raw objective value stays off the dashboard, and no
    line in either view carries the objective adjacent to "xPts"."""
    src = _strip_comments(VIEWS["home"])
    assert "rank_mv" not in src, (
        "the solver's unit must come from the payload's objective_mode, "
        "never be hardcoded — a hardcoded label survives an objective change"
    )
    assert "gain_over_roll" in src, (
        "the solver card must render the plan's own gain_over_roll — the "
        "solver's forecast, not a read-side re-derivation"
    )
    assert "solver forecast" in src, (
        "the gain line must carry the house label 'solver forecast'; an "
        "unlabelled gain reads as consensus xPts, which is the silent blend"
    )
    assert "objective_mode" in src, (
        "the gain's currency label must come from the payload's "
        "objective_mode, never be assumed"
    )
    for ln in src.splitlines():
        if "gain_over_roll" in ln:
            assert "consensus" not in ln, (
                f"the solver's gain summed or printed beside a consensus "
                f"number — the silent blend: {ln.strip()[:90]}"
            )
    assert not re.search(r"plan\.objective\b", src), (
        "the raw objective value stays off the dashboard (gain_over_roll is "
        "the served delta); the Solver tab speaks in the solver's currency"
    )
    # The solver lives in the Planner tab (one tab, fplreview's idiom); the
    # from-scratch Solver view is gone and must not come back beside it.
    assert "solver" not in VIEWS, "solver.js is folded into planner.js"
    assert 'href="#solver"' not in HTML and 'register("solver"' not in HTML
    planner_src = _strip_comments(VIEWS["planner"])
    assert "objective_mode" in planner_src, (
        "the Planner tab must print the objective in the payload's own unit"
    )
    for name in ("home", "planner"):
        for ln in _strip_comments(VIEWS[name]).splitlines():
            if "objective" in ln:
                assert "xPts" not in ln, (
                    f"solver objective rendered adjacent to 'xPts' — the "
                    f"silent blend ({name}): {ln.strip()[:90]}"
                )


def test_the_solve_plan_gain_travels_with_its_currency_label() -> None:
    """The no-silent-blend rule extended to the transfer plan: the brief's
    solve block now serves `plan` — the transfer_plan.json artefact rendered —
    and its gain_over_roll is the SOLVER'S OWN forecast, labelled by
    objective_mode in the same payload. The old read-side ideal-squad diff
    (`derived`, consensus_xpts_*) is gone from the schema entirely, so a view
    can no longer quote a consensus delta as if the solver said it."""
    solve = _schema_props("dashboard_brief", "solve")
    assert "plan" in solve, "the solve block must carry the transfer plan"
    assert "derived" not in solve, (
        "the ideal-squad diff block is cut; the plan's own moves are the card"
    )
    for gone in ("objective", "hold_baseline", "n_sims", "solver", "chip_gw"):
        assert gone not in solve, f"stale gw1_plan field {gone} survives"
    plan = _schema_props("dashboard_brief", "solve", "plan")
    assert {"objective_mode", "gain_over_roll", "moves", "is_roll",
            "captain", "your_captain", "alternatives",
            "hit_verdict"} <= plan, (
        "the plan payload must label its currency (objective_mode) beside "
        "the gain, and a roll must be a flagged recommendation"
    )
    assert not {k for k in plan if k.startswith("consensus")}, (
        "no consensus number may live inside the solver's plan payload"
    )


def test_idea_due_left_the_tile_vocabulary() -> None:
    """The idea registry is out of briefings: the tile kind enum must not
    offer it, so no view can render a tile the brief will never serve."""
    node = get_script("dashboard_brief").result_schema["oneOf"][0]
    kinds = node["properties"]["tiles"]["items"]["properties"]["kind"]["enum"]
    assert "idea_due" not in kinds


def test_the_pitch_fallback_is_the_clubmark_discipline() -> None:
    """Photo 404 → ONE class flip reveals a club-coloured monogram in the
    identical CSS-sized box: zero reflow, complete offline. Structural scan:
    the flip, the monogram element, and the CSS that keeps the box."""
    body = _nested_fn_body(_strip_comments(VIEWS["home"]), "pcard")
    assert 'classList.add("fall")' in body, "the error handler must flip one class"
    assert "pp-mg" in body, "the monogram element must exist under the photo"
    assert "dataset.club" in _strip_comments(VIEWS["home"]), (
        "club colours key off data-club (clubmark.css map)"
    )
    css = (WEB / "dashboard.css").read_text()
    assert re.search(r"\.pp-face\s*\{[^}]*height:\s*64px", css), (
        "the photo box must be CSS-sized before load"
    )
    assert re.search(r"\.pp\.fall \.pp-face\s*\{[^}]*visibility:\s*hidden", css), (
        "the failed photo hides via visibility, never display — display:none "
        "would reflow"
    )
    assert re.search(r"\.pp\.fall \.pp-mg\s*\{[^}]*position:\s*absolute", css), (
        "the monogram overlays the same box; it must not push content"
    )


def test_the_dashboard_hardcodes_no_gate_thresholds() -> None:
    """The brief echoes its thresholds; the view renders the served `gate`
    strings and threshold fields, never its own constants. Pin the two
    magic numbers most likely to be re-hardcoded."""
    src = _strip_comments(VIEWS["home"])
    assert "thresholds" in src, "the view must read the brief's threshold echo"
    assert not re.search(r"[^\d.]0\.5\b.*xPts", src), (
        "the bench margin lives in the brief's thresholds, not the view"
    )
    assert "t.gate" in src, "tiles must print the served gate string"


def test_the_deadline_is_fetched_never_hardcoded() -> None:
    assert not re.search(r'new Date\("\d{4}-\d{2}-\d{2}T', ALL_JS + HTML), (
        "a hardcoded deadline literal is back; it expires and then counts up "
        "forever"
    )
    assert "/api/deadline" in APP


def test_the_clock_never_hardcodes_a_gameweek_label() -> None:
    """The countdown once said "GW1 ... Fri 21 Aug" forever, from prose baked
    into tick()."""
    assert "GW1 deadline" not in ALL_JS
    assert re.search(r"GW\$\{", APP), "the GW label must come from the fetch"


def test_the_fixtures_caption_flips_on_data_not_prose() -> None:
    """Difficulty is optional in the schema (the cached artefact may be
    absent); the view must carry BOTH captions and choose by inspecting the
    payload, never claim colouring it cannot have.

    HONEST LIMIT: this is a source scan, not a dataflow proof. It can catch a
    derived ease value written without a null guard on its own line; it cannot
    prove the guard is reached, and it is satisfiable by code that looks right
    and behaves wrongly. The real guarantee is the browser check that an
    unfitted fixture renders hatched. Two earlier versions of this assertion
    passed against deliberately broken code -- one matched the guard on an
    unrelated line, the next matched a ternary -- so treat a pass here as a
    smoke test.
    """
    props = _schema_props("fixture_board", "teams", "fixtures", "opponents")
    assert "legacy_difficulty" in props, (
        "the optional legacy_difficulty field left the schema -- with the "
        "ticker deleted it is the only blend the view can fall back to"
    )
    src = VIEWS["fixtures"]
    # Assert the BEHAVIOUR, not the identifier. This pinned a variable named
    # `anyDifficulty`, so rebuilding the view broke the test while the
    # behaviour it protects survived intact. A test that names a local is a
    # test that fails on a rename and passes on a regression.
    assert "difficulty" in src, "the view must read the difficulty field"
    # It must say so when the artefact is absent rather than colouring anyway.
    assert re.search(r"no (difficulty|fit|fitted)", src, re.I), (
        "the view must carry a caption for the no-artefact case"
    )
    # The property that matters is ABSENCE PRODUCES NO COLOUR. A regex over
    # source cannot follow dataflow -- the view reads the field into a local
    # and gates on that -- so assert the shape of the guard rather than the
    # name it is applied to: somewhere, a missing value must yield null rather
    # than a number that would be coloured.
    # Target the ACTUAL computation. A file-wide search for the guard passed
    # even after the guard on this line was replaced with a 0.5 default --
    # the pattern matched somewhere else. A test that can be satisfied by an
    # unrelated line is not testing the thing it names.
    # Anchored to an object-literal key at line start. An unanchored search
    # matched the colon of a TERNARY (`a != null ? a : b`) and reported it as
    # an ungated computation.
    ease = [ln for ln in src.splitlines() if re.match(r"\s*ease\w*\s*:", ln)]
    assert ease, "the view no longer computes an ease value under that name"
    derived = [ln for ln in ease if re.search(r"[-*/+]", ln)]
    assert derived, "no ease value is derived from anything"
    for ln in derived:
        assert re.search(r"==\s*null\s*\?\s*null", ln), (
            "an ease value derived without a null guard colours a fixture the "
            f"model has no rating for: {ln.strip()[:90]}"
        )
    # And the reader must be told which absence they are looking at: a blank
    # gameweek and an unfitted fixture are different answers.
    assert re.search(r"\bblank\b", src, re.I), (
        "a blank gameweek must be labelled as such, not shown as easy"
    )


def test_sorting_rebuilds_the_tbody_not_the_panel() -> None:
    """host.innerHTML="" on a header click deleted the provenance footer.

    The second half of this test used to read app.js's ``dataTable`` helper.
    That helper was exported and called by zero views: every tab hand-rolled
    its own table, which is why three of them diverged on header casing and
    cell padding while a shared implementation sat unused. It is deleted, so
    the invariant is asserted where tables are actually built.
    """
    assert 'host.innerHTML=""' not in ALL_JS.replace(" ", "")
    assert "dataTable" not in APP, (
        "app.js exports a table helper again: either every view uses it or "
        "it does not exist, but an unused shared component is how the views "
        "drift apart"
    )
    for name, src in VIEWS.items():
        if "tbody" not in src:
            continue
        assert "tbody.innerHTML" not in src, (
            f"{name} rebuilds a table body with innerHTML; use "
            f"tbody.textContent so nothing outside the body is destroyed"
        )


def test_the_chat_subapp_is_built_and_the_view_mounts_it() -> None:
    """The chat tab is the one built sub-app (CHAT_ARCHITECTURE §5): source in
    web/chat-app/, committed build in web/dist/chat-app/ with fixed asset
    names. A missing build would render an empty tab silently; fail loudly."""
    bundle = WEB / "chat-app" / "assets" / "index.js"
    css = WEB / "chat-app" / "assets" / "index.css"
    assert bundle.is_file() and bundle.stat().st_size > 10_000, (
        "web/dist/chat-app/assets/index.js is missing or empty — run "
        "`npm run build` in web/chat-app/"
    )
    assert css.is_file(), (
        "web/dist/chat-app/assets/index.css did not build; the pane would "
        "mount unstyled"
    )
    assert "/chat-app/" in VIEWS["chat"], (
        "the chat view must mount the built sub-app from /chat-app/"
    )


def test_the_pipeline_row_model_reads_only_board_fields() -> None:
    """`rowModel()` is the pipelines view's flatten(): the single place its
    reads meet pipeline_board's row schema. A field it reads that the schema
    does not carry silently becomes undefined -- the exact bug class the
    fixtures adapter shipped once already."""
    allowed = _schema_props("pipeline_board", "rows")
    body = _strip_comments(_fn_body(VIEWS["pipelines"], "rowModel"))
    used = set(re.findall(r"\br\.(\w+)", body))
    unknown = used - allowed
    assert not unknown, (
        f"rowModel reads {sorted(unknown)}, which the pipeline_board row "
        f"schema does not carry (it has {sorted(allowed)}); those render as "
        f"undefined"
    )
    # The nested blocks are why the adapter exists at all.
    for nested in ("health", "metered", "last_run", "runs"):
        assert nested in allowed and f"r.{nested}" in body, (
            f"rowModel must read r.{nested}; the board nests its contract "
            "and a flat read would silently miss it"
        )


def test_the_pipeline_health_dot_never_travels_without_its_reason() -> None:
    """The reason sentence is the product; a bare red dot is the failure this
    panel exists to prevent. The one function that renders health must render
    the reason string alongside the dot."""
    body = _fn_body(VIEWS["pipelines"], "healthEl")
    # Repointed 2026-09-18: the dot is drawn by chip(), which the status and
    # stale columns share. The intent is unchanged, so it is now checked in
    # two halves: healthEl renders the reason beside a chip, and chip is
    # where pipe-dot lives.
    assert "chip(" in body and "reason" in body, (
        "healthEl must render both the state chip and md.reason; a dot "
        "without its sentence is decoration"
    )
    assert "pipe-dot" in _fn_body(VIEWS["pipelines"], "chip"), (
        "chip() must draw the dot; it is the only thing that does"
    )


def test_the_metered_confirm_is_inline_never_a_browser_dialog() -> None:
    """PIPELINES.md §5 decision 4: a metered trigger shows credits and month
    spend BEFORE the click confirms. window.confirm() can quote neither, so
    its presence would mean the cost gate was replaced with a speed bump."""
    src = _strip_comments(VIEWS["pipelines"])
    assert "needs_confirm" in src, (
        "the view must handle the route's needs_confirm payload"
    )
    for field in ("credits_estimate", "month_spend"):
        assert field in src, (
            f"the confirm strip must render {field}; a confirmation that "
            "quotes no cost is not a confirmation"
        )
    assert not re.search(r"(?<![.\w])confirm\s*\(", src) \
        and "window.confirm" not in src, (
        "a browser confirm() dialog is back; the confirm strip must live on "
        "the row with the numbers in it"
    )


def test_pipeline_timestamps_are_relative_with_the_absolute_in_title() -> None:
    """Every timestamp reads '12m ago' with the exact instant in its title.
    Source scan, not a dataflow proof -- but both halves must at least exist
    and be used together somewhere."""
    src = _strip_comments(VIEWS["pipelines"])
    assert re.search(r"function relTime\(", src), "the relative formatter left"
    assert re.search(r"function absTime\(", src), "the absolute formatter left"
    assert re.search(r"\.title\s*=[^;]*absTime\(", src), (
        "no element carries the absolute instant in its title; a relative "
        "time with no absolute anywhere is unverifiable"
    )


def test_no_raw_html_from_model_or_panel_output() -> None:
    """Chat and panels render server/model text; innerHTML on raw output is an
    injection seam. textContent/createTextNode only, except vetted literals."""
    for name, src in VIEWS.items():
        for m in re.finditer(r"\.innerHTML\s*=\s*(.+)", src):
            rhs = m.group(1).strip()
            assert rhs.startswith('"') or rhs.startswith("'") or rhs.startswith("`") and "${" not in rhs, (
                f"view {name} assigns dynamic innerHTML: {rhs[:60]}"
            )


# -- the dashboard after GW4: one lineup, no stale guidance, gaps first -------


_JS_STRING = re.compile(
    r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`(?:[^`\\]|\\.)*`', re.S)


def test_the_pitch_draws_one_lineup_with_no_toggle_and_no_arrows() -> None:
    """The owner: "just show the best XI, no arrows". The pitch reads the
    brief's best_xi block and nothing suggests a second lineup."""
    src = _strip_comments(VIEWS["home"])
    for gone in ("As picked", "Suggested XI", "db-toggle", "pp-mark", "⇄",
                 "suggested_xi", "pitchMode"):
        assert gone not in src, f"the two-lineup pitch is back: {gone!r}"
    for read in ("best_xi", "captain_candidates", "close_call", "n_differs",
                 "captain_close_call_xpts"):
        assert read in src, f"the pitch must read best_xi.{read}"


def test_the_best_xi_and_squad_source_read_only_schema_fields() -> None:
    src = _strip_comments(VIEWS["home"])
    for var, node in (("bestXi", "best_xi"), ("squadSource", "squad_source")):
        allowed = _schema_props("dashboard_brief", node)
        used = set(re.findall(rf"\b{var}\.(\w+)", src))
        unknown = used - allowed
        assert not unknown, (
            f"{var} reads {sorted(unknown)}, which the brief's {node} schema "
            f"does not carry (it has {sorted(allowed)})")


def test_a_stale_or_missing_plan_renders_no_plan_content() -> None:
    """With solve.state stale/missing the card is state, reason, age and
    Re-run. Plan fields are read only inside renderSolver, behind the
    fresh/aging guard; nothing else on the page touches them."""
    src = _strip_comments(VIEWS["home"])
    body = _nested_fn_body(src, "renderSolver")
    guard = re.search(r'const plan = \(S && \(S\.state === "fresh" \|\| '
                      r'S\.state === "aging"\)\)', body)
    assert guard, "the plan body must be null unless the plan is fresh/aging"
    outside = src.replace(body, "")
    leaks = re.findall(r"\bplan\.(captain|moves|alternatives|gain_over_roll)",
                       outside)
    assert not leaks, f"plan content rendered outside the solver card: {leaks}"
    assert "free_transfers_state" in src and '"?"' in src, (
        "a stale plan's free-transfer count must print as unknown")


def test_haul_odds_are_gated_on_the_simulation_date() -> None:
    """Haul odds come from a simulation with a date; they render only when
    it is newer than the last deadline, else the omission carries a tooltip
    naming the simulation date. Never a number from an older world."""
    src = _strip_comments(VIEWS["home"])
    assert "p_haul_generated" in src and "last_deadline_utc" in src
    assert "haul odds unavailable (last simulation" in src
    # every p_haul render site sits behind the freshness flag
    for m in re.finditer(r"Math\.round\((\w+(?:\.\w+)*)\s*\*\s*100\)", src):
        expr = m.group(1)
        if "haul" not in expr:
            continue
        # the gate is either an inline `haulFresh &&` or an early return
        # a few lines up; one render site per ~500 chars in this file
        window = src[max(0, m.start() - 520):m.start()]
        assert "haulFresh" in window, (
            f"a haul percentage renders without the freshness gate: {expr}")


def test_the_gaps_strip_is_payload_derived_and_names_every_fix() -> None:
    src = _strip_comments(VIEWS["home"])
    body = _nested_fn_body(src, "renderGaps")
    assert "Before you read this" in body
    # the squad fix is the brief's own command, never typed here
    assert "squadSource.fix" in body
    assert "myteam auth" not in src, "the fix command comes from the payload"
    # every gap kind reads its own source
    for read in ("squadSource.live", "S.state", "solveStatus", "intelOutdated()",
                 "xpts_as_of", "haulFresh"):
        assert read in body, f"the gaps strip must read {read}"
    # the wired fixes
    assert "rerunButton(" in body and 'generateBtn("Regenerate")' in body
    # nothing renders when there is no gap
    assert "gapStrip.hidden = true" in body


def test_an_outdated_briefing_folds_and_never_renders_items_as_current() -> None:
    src = _strip_comments(VIEWS["home"])
    body = _nested_fn_body(src, "renderIntel")
    assert "intelOutdated()" in body and "ib-outdated" in body
    assert "Briefing outdated (written" in src
    # the items go into the fold (body), not the card, when outdated
    assert re.search(r"body\.appendChild\(\s*intelItem", body)


def test_no_em_dashes_in_dashboard_strings() -> None:
    """The owner's prose rule (prose_style.py) applies to the UI: no em-dash
    asides in any string the dashboard prints."""
    src = VIEWS["home"]
    bad = [m.group(0)[:60] for m in _JS_STRING.finditer(_strip_comments(src))
           if "—" in m.group(0)]
    assert not bad, f"em-dashes in dashboard strings: {bad}"


def test_the_planner_tab_carries_the_solver_rail_and_fills_the_grid() -> None:
    """One tab: the rail's options are the runner's TRANSFER_DEFAULTS keys,
    Solve posts mode=transfers with them, the headline plan is drawn into the
    grid by the planner's own move machinery, and a stale plan is a gap with
    Re-solve rather than guidance."""
    from fpl_edge.platform import solve_runner
    src = _strip_comments(VIEWS["planner"])
    assert 'runPanel("planner_grid"' in src
    assert 'postJSON("/api/solve", { mode: "transfers", options: solveOptions() })' in src
    for key in solve_runner.TRANSFER_DEFAULTS:
        assert key in src, f"the rail must carry solve option {key}"
    assert 'getJSON("/api/solve/transfer-plan")' in src
    assert 'getJSON("/api/solve/status")' in src
    # the plan reaches the grid only through applyMove -> moves -> sanitise
    assert "function applyMove(" in src and "sanitise()" in src
    assert "tplan.stale" in src and "Re-solve" in src
    # the unconstrained best and chip plans are labelled, never mixed in
    assert "plan.unconstrained" in src and "chip plan" in src
    # no em-dash asides in authored strings (the owner's prose rule)
    bad = [m.group(0)[:60] for m in _JS_STRING.finditer(src) if "\u2014" in m.group(0)]
    assert not bad, f"em-dashes in planner strings: {bad}"


def test_the_dashboard_names_the_unconstrained_best_beside_the_headline() -> None:
    src = _strip_comments(VIEWS["home"])
    assert "if hits were free" in src
    assert 'href = "#planner"' in src and 'href="#solver"' not in src
    assert "#solver" not in src, "no link may point at the removed Solver tab"


# -- the pipelines tab: vocabulary, prose, CSS namespace, keyboard ------------


def _css_classes(path: Path) -> set[str]:
    return set(re.findall(r"\.([a-zA-Z][\w-]*)", path.read_text()))


def test_pipeline_styles_share_no_class_with_the_planner() -> None:
    """Both stylesheets are loaded globally on every tab, so a class defined
    in both is decided by load order, not by intent. It happened: `.pl-gap`
    and `.pl-log` were planner's on the pipelines page. The prefixes must not
    overlap."""
    def prefixed(name: str) -> set[str]:
        return {c for c in _css_classes(WEB / name)
                if c.startswith(("pl-", "pipe-"))}

    pipe, planner = prefixed("pipelines.css"), prefixed("planner.css")
    assert not (pipe & planner), (
        f"pipelines.css and planner.css both define {sorted(pipe & planner)}; "
        "whichever loads last wins on both tabs"
    )
    assert pipe and not [c for c in pipe if c.startswith("pl-")], (
        "the pipelines prefix must not be `pl-`; planner owns it"
    )


def test_every_pipeline_state_and_status_has_words_and_a_dot() -> None:
    """A state or ledger status with no entry in the lookup prints its own
    snake_case enum at the reader. Both vocabularies come from the panel's
    declared schemas, so a new enum server-side fails here first."""
    src = VIEWS["pipelines"]
    board = get_script("pipeline_board").result_schema
    board = board["oneOf"][0] if "oneOf" in board else board
    row = board["properties"]["rows"]["items"]["properties"]
    states = row["health"]["properties"]["state"]["enum"]
    statuses = row["runs"]["items"]["properties"]["status"]["enum"]

    def entries(name: str) -> set[str]:
        m = re.search(rf"const {name} = \{{(.*?)\}};", src, re.S)
        assert m, f"{name} not found in the pipelines view"
        return set(re.findall(r"(\w+):", m.group(1)))

    for name, wanted in (("STATE_DOT", states), ("STATE_WORD", states),
                         ("RUN_DOT", statuses), ("RUN_WORD", statuses)):
        missing = set(wanted) - entries(name)
        assert not missing, f"{name} has no entry for {sorted(missing)}"
    # The words are English, never the enum itself.
    for word in ("no_source", "skipped_fresh"):
        m = re.search(r"const RUN_WORD = \{(.*?)\};", src, re.S)
        assert f'"{word}"' not in m.group(1), (
            f"RUN_WORD renders the raw enum {word}"
        )


def test_pipeline_rows_are_operable_and_labelled() -> None:
    """The rows are buttons opening an expandable: they need the role, both
    activation keys, a named region the focus actually moves into, and a way
    back out. The columns need names, since they fold to a card.

    Repointed 2026-09-18: the drawer became a per-row expandable, so the
    dialog assertions became the disclosure ones. The intent survives
    verbatim: a row is operable from the keyboard, it says whether it is
    open, and focus goes into what it opened and comes back when it closes.
    """
    src = _strip_comments(VIEWS["pipelines"])
    assert 'setAttribute("role", "button")' in src, "rows need a button role"
    assert re.search(r'e\.key [!=]== " "', src), (
        "Space must activate a row, not scroll the page"
    )
    assert 'setAttribute("aria-expanded"' in src, (
        "a row that opens something must say whether it is open"
    )
    assert 'setAttribute("aria-controls"' in src, (
        "the row must name the expandable it controls"
    )
    assert 'setAttribute("role", "region")' in src, (
        "the expandable is a named region"
    )
    assert re.search(r'first\.focus\(\)', src), (
        "focus must move into the expandable"
    )
    assert re.search(r'row\.focus\(\)', src), (
        "focus must return to the row when it closes"
    )
    assert "const COLUMNS = [" in src, "the table's columns must be named once"
    assert "dataset.label" in src, "each cell must carry its column's name"


def test_pipeline_prose_holds_the_house_style() -> None:
    """No em-dash asides and one placeholder glyph, in any string the
    pipelines tab prints."""
    src = _strip_comments(VIEWS["pipelines"])
    strings = [m.group(0) for m in _JS_STRING.finditer(src)]
    bad = [s[:60] for s in strings if "—" in s]
    assert not bad, f"em-dashes in pipelines strings: {bad}"
    curly = [s[:60] for s in strings if set(s) & set("“”‘’")]
    assert not curly, f"curly quotes in pipelines strings: {curly}"
    assert 'const NONE = "–"' in VIEWS["pipelines"], (
        "the one placeholder is an en dash"
    )
    # A fallback glyph reads as data; four of them read as four things.
    fallbacks = re.findall(r"""(?:\?\?|\|\||:)\s*("[^"]*")""", src)
    stray = [f for f in fallbacks if f.strip('"') in ("?", "—", "null")]
    assert not stray, (
        f"a second placeholder glyph is back: {stray}; NONE is the only one"
    )


def test_pipeline_times_use_the_shared_span_vocabulary() -> None:
    """"43h" and "86m" are arithmetic, not a reading. relTime affixes the
    direction onto app.js's fmtSpan/fmtAge and computes no ladder of its
    own."""
    src = VIEWS["pipelines"]
    assert "fmtSpan" in src and "fmtAge" in src, (
        "the shared helpers must be imported, not re-implemented"
    )
    body = _fn_body(src, "relTime")
    assert "fmtSpan(" in body and "fmtAge(" in body
    assert "60" not in body, "relTime is computing its own units again"


# --------------------------------------------------------------------------
# The Account card and the Planner's run/plan seam, both exercised rather than
# only scanned. The zero-build views are ES modules, so a tiny DOM shim and
# `node` render them for real: a crafted status object reaches the same code
# the browser runs, and no real token is touched.
# --------------------------------------------------------------------------

import json
import shutil
import subprocess

_SHIM = """
class N {
  constructor(tag) { this.tag = tag; this.className = ""; this.attrs = {};
                     this.kids = []; this._t = ""; }
  set textContent(v) { this._t = v == null ? "" : String(v); this.kids = []; }
  get textContent() {
    return this._t + this.kids.map(k => typeof k === "string" ? k : k.textContent).join("");
  }
  appendChild(n) { this.kids.push(n); return n; }
  append(...xs) { for (const x of xs) this.kids.push(x); }
  setAttribute(k, v) { this.attrs[k] = v; }
  querySelectorAll() { return []; }
}
globalThis.document = { createElement: t => new N(t) };
function walk(n, out) {
  if (typeof n === "string") { out.push({ cls: "", text: n }); return out; }
  out.push({ cls: n.className || "", text: n.textContent });
  for (const k of n.kids) walk(k, out);
  return out;
}
"""


def _render(tmp_path, view: str, body: str) -> dict:
    """Render `view` in node with a DOM shim; `body` leaves `result` set."""
    node = shutil.which("node")
    if not node:                       # the suite must not need a JS runtime
        import pytest
        pytest.skip("node is not installed")
    # `.mjs` so node reads both as modules without a package.json of its own
    shutil.copy(WEB / "js" / "app.js", tmp_path / "app.mjs")
    src = (WEB / "js" / "views" / f"{view}.js").read_text()
    (tmp_path / f"{view}.mjs").write_text(src.replace('"/js/app.js"', '"./app.mjs"'))
    (tmp_path / "run.mjs").write_text(
        _SHIM
        + f'const view = await import("./{view}.mjs");\n'
        + body
        + "\nconsole.log(JSON.stringify(result));\n"
    )
    done = subprocess.run([node, str(tmp_path / "run.mjs")],
                          capture_output=True, text=True, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


#: A revoked grant, as `/api/account/status` reports it: the refresh token is
#: still stored and its own `exp` is months away, the last success still names
#: the team, and the ONLY contrary signal is `last_attempt.ok = false`. This is
#: the shape that used to render "Connected as i-test" under a green dot.
_REVOKED = {
    "ok": True, "entry_id": 4490171, "refresh_stored": True,
    "access": {"expires_at": "2026-09-08T10:37:17+00:00", "expired": True, "days_left": 0},
    "refresh": {"expires_at": "2027-03-07T02:37:17+00:00", "expired": False, "days_left": 179},
    "last_ok": {"at": "2026-09-05T02:37:18+00:00", "entry_id": 4490171,
                "entry_name": "i-test", "player_name": "Nripesh Pradhan"},
    "last_attempt": {"at": "2026-09-08T02:37:18+00:00", "ok": False,
                     "stage": "refresh", "error_class": "refresh_refused"},
    "squad_source": "public",
    "squad_source_reason": "the last live check failed, so the panels fall back "
                           "to public picks until a verify succeeds",
    "summary": "access token expired (exp 2026-09-08 10:37Z); refresh token "
               "unexpired (exp 2027-03-07 02:37Z, 179 days left; the issuer can "
               "still revoke it)",
}


def test_a_revoked_grant_reads_as_broken_not_as_connected(tmp_path) -> None:
    """The blocker: `refresh_stored && squad_source == "private"` decided the
    headline and `last_ok.entry_name` filled it, so a revoked grant kept its
    green dot and its team name while the only true signal sat in grey at the
    bottom. The last attempt now decides the headline."""
    nodes = _render(tmp_path, "account",
                    f"const host = document.createElement('div');\n"
                    f"view.renderStatus(host, {json.dumps(_REVOKED)});\n"
                    f"const result = walk(host, []);")
    text = nodes[0]["text"]
    assert "Connected as" not in text, "a revoked grant still claims a connection"
    assert "Connection broken" in text
    assert "valid until" not in text, (
        "the refresh token's own expiry may not be printed as validity: the "
        "issuer revokes before `exp`, which is the whole defect"
    )
    # the server's own sentence, which the card never rendered
    assert _REVOKED["summary"] in text
    # the fix used to appear only after a second Verify
    assert "Log in again at fantasy.premierleague.com" in text
    # the enum stays out of the copy, in both directions
    assert "refresh_refused" not in text
    assert "the issuer refused the refresh token" in text
    # the state dot is the broken one, not the connected one
    dots = [n["cls"] for n in nodes if "acct-dot" in n["cls"]]
    assert dots == ["acct-dot off"], dots
    # and the failure is styled as an error, not as one more muted line
    assert any("acct-err" in n["cls"] for n in nodes), (
        "the failure line must not be the same grey as every other line"
    )


def test_a_working_connection_still_reads_as_connected(tmp_path) -> None:
    """The other half of the blocker: the broken branch must not swallow a
    live account. This is the live shape of `/api/account/status`."""
    ok = dict(_REVOKED,
              last_attempt={"at": "2026-09-08T02:37:18+00:00", "ok": True,
                            "stage": "done", "error_class": None},
              squad_source="private",
              squad_source_reason="live from your FPL account")
    nodes = _render(tmp_path, "account",
                    f"const host = document.createElement('div');\n"
                    f"view.renderStatus(host, {json.dumps(ok)});\n"
                    f"const result = walk(host, []);")
    text = nodes[0]["text"]
    assert "Connected as i-test (Nripesh Pradhan)" in text
    assert "Connection broken" not in text
    assert [n["cls"] for n in nodes if "acct-dot" in n["cls"]] == ["acct-dot on"]


def test_an_empty_submit_does_not_claim_a_read_failed(tmp_path) -> None:
    """Nothing was pasted, so nothing failed to be read, and no server stage
    ran to name. The sentence under the headline is prose, not a `pre`."""
    out = {"ok": False, "error_class": "empty_paste",
           "message": "Paste the Cookie header or the two tokens, then press "
                      "Verify and save."}
    nodes = _render(tmp_path, "account",
                    f"const host = document.createElement('div');\n"
                    f"view.renderOutcome(host, {json.dumps(out)});\n"
                    f"const result = walk(host, []);")
    text = nodes[0]["text"]
    assert "Nothing was pasted" in text
    assert "could not be read" not in text
    assert "stage" not in text, "the stage enum is out of the user-facing copy"
    src = VIEWS["account"]
    assert 'el("pre", "acct-msg"' not in src, "prose is a paragraph, not a pre"
    assert 'el("p", "acct-msg"' in src


def test_no_server_error_class_or_stage_enum_reaches_the_account_copy() -> None:
    """Internal identifiers stay internal: every branch reads the FAILURE /
    FAILURE_TITLE / STAGE tables rather than printing `out.error_class` or
    `out.stage` into a sentence."""
    src = _strip_comments(VIEWS["account"])
    assert "(stage: " not in src
    assert "${out.stage}" not in src and "${out.error_class}" not in src
    assert "${st.last_attempt.error_class" not in src
    assert "st.summary" in src, "the server's own summary must be rendered"


def test_the_account_card_folds_the_steps_and_footers_its_provenance() -> None:
    """Account was the only tab with no provenance footer, and it led with
    four DevTools steps while already connected."""
    src = _strip_comments(VIEWS["account"])
    assert "provenance" in src and "provenance({" in src
    assert 'el("details", "acct-stepfold")' in src
    assert 'steps.open = state === "absent"' in src
    assert "CARD_TITLE[state]" in src, "the card is titled by connection state"


def test_the_planner_does_not_reimplement_the_age_vocabulary() -> None:
    """"112.2h ago" is the format the owner asked to be removed. The planner
    imports fmtAge and keeps no ladder of its own."""
    src = VIEWS["planner"]
    imports = re.search(r"import \{(.*?)\} from \"/js/app\.js\"", src, re.S)
    assert imports and "fmtAge" in imports.group(1), (
        "the shared helper must be imported, not re-implemented"
    )
    assert "function ageText" not in src, "the local age helper is gone"
    assert "toFixed(1)}h ago" not in src


def test_the_planner_ties_the_run_status_to_the_plan_it_produced() -> None:
    """The rail printed one run's settings, times and log above a plan card
    built by a different run. The status block now says which."""
    src = _strip_comments(VIEWS["planner"])
    assert "function runOwnsPlan(" in src
    assert "generated_at" in _fn_body(src, "runOwnsPlan"), (
        "the test is the plan's own stamp against the run's window"
    )
    assert "Last run, not the run behind the plan card" in src
    assert "/position" not in src, (
        "the per-position candidate cap is not the size of the search and no "
        "longer rides in a settings headline"
    )
    assert "candidate moves solved in full" in src, (
        "the screened/solved counts belong beside the gain, not in a footer"
    )


def test_an_alternative_that_is_part_of_the_plan_is_labelled(tmp_path) -> None:
    """"Odegaard to Tavernier, +4.8" was listed as an alternative the plan
    beat while being exactly half of the plan's own two transfers."""
    chosen = {"out": [184029, 466052], "in": [201658, 243298], "n_transfers": 2}
    half = {"out": [184029], "in": [201658], "n_transfers": 1}
    other = {"out": [244850], "in": [201658], "n_transfers": 1}
    result = _render(tmp_path, "planner",
                     f"const result = {{"
                     f"half: view.subsetMove({json.dumps(half)}, {json.dumps(chosen)}),"
                     f"other: view.subsetMove({json.dumps(other)}, {json.dumps(chosen)}),"
                     f"self: view.subsetMove({json.dumps(chosen)}, {json.dumps(chosen)})}};")
    assert result["half"] is True, "half of the chosen move is part of it"
    assert result["other"] is False, "a genuine alternative is not"
    assert result["self"] is False, "the plan is not a subset of itself"
    assert "part of the headline plan" in VIEWS["planner"]


def test_the_must_keep_rail_says_when_nothing_is_selected() -> None:
    """Fifteen names under "locked in every gameweek" read as fifteen locks."""
    src = _strip_comments(VIEWS["planner"])
    assert "None selected: the solver may sell any of the" in src
    assert 'keepBox.classList.toggle("pl-none"' in src
    assert ".pl-toggles.pl-none" in (WEB / "planner.css").read_text(), (
        "the dimmed state needs the rule that dims it"
    )


def test_the_pool_heading_and_footer_count_the_same_set() -> None:
    """"ALL 639 PLAYERS" over "showing 40 of 637": the two players the plan
    buys were subtracted from one number and not the other."""
    src = _strip_comments(VIEWS["planner"])
    assert "res.candidates.length} players" not in src
    assert "players you do not hold in GW" in src
    assert "const addable = res.candidates.filter(c => !heldNow.has(c.code)).length" in src


# ======================================================== the Creators tab
#
# Ten defects an independent reviewer found against the live DOM. Each one had
# a symptom a screenshot could see and no test could, so each gets one here.

CREATORS = VIEWS["creators"]
CREATORS_CSS = (WEB / "creators.css").read_text()


def _js_string_literals(src: str) -> list[tuple[int, str]]:
    """Every string literal, with its line. Comments are not strings.

    A hand-rolled scan rather than a regex: the file mixes ', " and template
    literals, and the rendered prose is what the house rule governs, while the
    comments explaining that prose are not rendered at all.
    """
    out, i, n, line = [], 0, len(src), 1
    in_comment, quote, buf, start = False, None, [], 1
    esc = False
    while i < n:
        ch = src[i]
        if ch == "\n":
            line += 1
        if in_comment:
            if src.startswith("*/", i):
                in_comment = False
                i += 2
                continue
            i += 1
            continue
        if quote:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                out.append((start, "".join(buf)))
                quote = None
                i += 1
                continue
            else:
                buf.append(ch)
            i += 1
            continue
        if src.startswith("/*", i):
            in_comment = True
            i += 2
            continue
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if ch in "\"'`":
            quote, buf, start = ch, [], line
            i += 1
            continue
        i += 1
    return out


def test_no_rendered_string_in_the_creators_tab_carries_an_em_dash() -> None:
    """The owner's rule, enforced where the strings are written.

    "–" is exempt: it is the app's null glyph and its numeric range separator,
    a symbol rather than punctuation inside a sentence.
    """
    bad = [(line, text) for line, text in _js_string_literals(CREATORS)
           if "—" in text or " -- " in text]
    assert bad == [], f"rewrite the sentence, do not swap the punctuation: {bad}"


def test_plural_returns_null_on_a_null_count() -> None:
    """`creator_report_card` sends `n_total: null` for 9 of 31 cards. Four call
    sites interpolated it and the tab printed the literal string "null" nine
    times. Null in, null out, so no caller can stringify it by accident."""
    src = _strip_comments(CREATORS)
    assert "n == null ? null :" in src, "plural() must refuse a null count"
    # ...and nothing interpolates n_total into a template without a guard.
    assert not re.search(r"\$\{plural\([^)]*n_total", src), (
        "a null count reached a template again")
    assert not re.search(r"\$\{c?l?\.?claims?\.n_total\}", src), (
        "n_total interpolated bare")


def test_an_unscored_record_falls_back_to_the_payloads_reason() -> None:
    """The count is not always available; the reason always is."""
    src = _strip_comments(CREATORS)
    assert "function unscored(cl, tail, short)" in src
    # every one of the four sites goes through it
    assert src.count("unscored(") >= 5, "a site still formats its own count"


def test_the_team_verdict_is_gated_on_quotable_not_on_a_null_boolean() -> None:
    """`min_gw_measured` is 10 and every measured person has n_gw 3 with
    `quotable: false`. The card rendered "beats cohort: yes" directly above the
    reason saying three gameweeks is below the floor."""
    src = _strip_comments(CREATORS)
    assert "p.beats_baseline == null ? `under the" not in src
    assert 'fact("beats the baseline", p.quotable' in src, (
        "the floor decides this, not the boolean")
    # the compact card qualifies it too, rather than printing a bare delta
    assert "const one = p => p.quotable" in src


def test_the_under_floor_class_is_actually_styled() -> None:
    """`verdict()` appended a `few` class below the floor and nothing styled
    it, so a verdict from 12 claims was painted the same full red as one from
    274 and only an 11.5px word differed."""
    assert ".few" in CREATORS_CSS, "the class the JS emits has no rule"
    for sel in (".cx-rc.few", ".cx-rctag.few", ".cx-rcard.few"):
        assert sel in CREATORS_CSS, f"{sel} carries colour that the count denies"
    # the report card must actually receive the class
    src = _strip_comments(CREATORS)
    assert 'el("button", "cx-rcard " + v.cls' in src
    assert 'el("button", "cx-rcard " + coin(' not in src


def test_one_creator_count_is_drawn_and_it_says_what_the_others_are() -> None:
    """20, 31 and 28 for one population, twelve pixels apart and unlabelled."""
    src = _strip_comments(CREATORS)
    assert "function cardCensus()" in src and "function censusLine()" in src
    assert "censusLine()} · floor" in src
    assert 'plural((rc.cards || []).length, "creator")' not in src, (
        "the raw card count is back in the heading")


def test_the_board_scope_and_its_own_note_are_rendered() -> None:
    """`scope.applied`, `scope.excluded` (4 sources), `record_note` and
    `mine_reason` were all computed by the panel and read nowhere."""
    src = _strip_comments(CREATORS)
    for key in ("res.scope", "sc.excluded", "res.record_note", "res.mine_reason"):
        assert key in src, f"the panel computes {key} and the page drops it"
    assert "function scopeLine()" in src


def test_the_source_button_does_not_call_a_filtered_list_all_of_them() -> None:
    """HIDDEN_STATES drops 3 of 43 sources and the button said "all 40"."""
    src = _strip_comments(CREATORS)
    assert "`all ${shown.length} sources`" not in src
    assert "fetchable sources" in src
    assert "const dropped = total - shown.length" in src


def test_the_record_strip_draws_no_permanently_empty_group() -> None:
    """"Record leaders" whose only possible content was "nobody above chance",
    beside "Laggards", which is a judgment this surface otherwise avoids."""
    src = _strip_comments(CREATORS)
    assert "Laggards" not in src and "Record leaders" not in src
    assert "Interval below a coin flip" in src
    assert "if (!list.length) return null;" in src, "an empty group is still drawn"
    # and the strip reconciles its two chips with the honesty line's three
    assert "of them over the floor" in src


def test_the_compact_team_line_is_capped_at_two_people() -> None:
    """Fantasy Football Hub has seven; they rendered on one 400-character
    line that never wrapped."""
    src = _strip_comments(CREATORS)
    assert "const SHOWN = 2;" in src
    assert "more, in the card" in src


def test_a_repeated_fpl_entry_is_named_as_a_repeat() -> None:
    """Three cards resolve to entry 176749 and drew three identical team
    results with nothing saying it is one person counted three times."""
    src = _strip_comments(CREATORS)
    assert "cardsByEntry" in src
    assert "also on ${also.join" in src or "`also on ${also.join(\", \")}`" in src
    assert ".cx-samewho" in CREATORS_CSS


def test_the_age_helper_is_the_shared_one() -> None:
    """`relAge` introduced "yesterday" and "2mo ago", words no other tab uses.
    The span now comes from app.js; only the freshness class stays local."""
    src = _strip_comments(CREATORS)
    assert re.search(r"import \{[^}]*\bfmtAge\b[^}]*\} from \"/js/app\.js\"", src), (
        "the shared span helper is not imported")
    assert "fmtAge(iso)" in src, "the local helper still writes its own words"
    assert "yesterday" not in src
    assert "mo ago" not in src
    assert "export function fmtAge" in APP, "the shared helper must still exist"


def test_a_failed_panel_body_is_folded_not_interpolated() -> None:
    """`errBox` printed the whole response body and the honesty line put it
    mid-sentence, so a traceback landed inside a paragraph."""
    src = _strip_comments(CREATORS)
    assert "const statusLine =" in src and "function failFold(" in src
    assert "`The record could not be read: ${rcErr}`" not in src
    assert "failFold(rcErr" in src
    assert "errBox(rcErr)" not in src, "the whole body is printed again"
    assert "${rcErr}" not in src, "the whole body is interpolated again"
    assert ".cx-raw" in CREATORS_CSS, "the folded body needs somewhere to sit"


def test_the_loading_affordance_has_one_glyph_and_one_capitalisation() -> None:
    src = _strip_comments(CREATORS)
    assert "Measuring the record…" not in src, (
        "the same fact was capitalised two ways twelve pixels apart")
    assert src.count("measuring the record…") >= 3
    # one glyph: the single character "…", never a trailing "..."
    dotted = [(line, text) for line, text in _js_string_literals(CREATORS)
              if re.search(r"[A-Za-z]\.\.\.(?!\.)\s*$", text)]
    assert dotted == [], f"the ellipsis is spelled with dots: {dotted}"
