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
    # The view falls back to fixture_ticker when the split artefact is absent,
    # so the union of the two is what it may legitimately see. Anything outside
    # that union is a field no registered panel publishes.
    allowed = (_schema_props("fixture_board", "teams", "fixtures", "opponents")
               | _schema_props("fixture_ticker", "teams", "fixtures", "opponents"))
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
    """The no-silent-blend rule (FINAL_SPEC Kill 3), updated for the minimal
    solver card: the DASHBOARD no longer renders the solver's objective at
    all — the one gain line is the derived consensus delta, labelled as
    derived, and the objective lives on the Solver tab in its own currency.
    So: no hardcoded unit, no objective value on the dashboard, and no line
    anywhere in either view carrying the objective adjacent to "xPts"."""
    src = _strip_comments(VIEWS["home"])
    assert "rank_mv" not in src, (
        "the solver's unit must come from the payload's objective_mode, "
        "never be hardcoded — a hardcoded label survives an objective change"
    )
    assert "plan.objective" not in src, (
        "the dashboard's minimal solver card renders no objective; the "
        "Solver tab is where the solver speaks in its own currency"
    )
    solver_src = _strip_comments(VIEWS["solver"])
    assert "objective_mode" in solver_src, (
        "the Solver tab must print the objective in the payload's own unit"
    )
    for name in ("home", "solver"):
        for ln in _strip_comments(VIEWS[name]).splitlines():
            if "objective" in ln:
                assert "xPts" not in ln, (
                    f"solver objective rendered adjacent to 'xPts' — the "
                    f"silent blend ({name}): {ln.strip()[:90]}"
                )
    # and the derived consensus line must confess whose voice it is
    assert "not the solver objective" in src


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
    props = _schema_props("fixture_ticker", "teams", "fixtures", "opponents")
    assert "difficulty" in props, "the optional difficulty field left the schema"
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
    """host.innerHTML="" on a header click deleted the provenance footer."""
    assert 'host.innerHTML=""' not in ALL_JS.replace(" ", "")
    body = _fn_body(APP, "renderBody")
    assert "tbody.textContent" in body, (
        "sorting must clear and rebuild only the tbody"
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
    assert "pl-dot" in body and "reason" in body, (
        "healthEl must render both the dot and md.reason; a dot without its "
        "sentence is decoration"
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
