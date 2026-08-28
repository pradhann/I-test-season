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


def test_the_movers_renderer_reads_what_the_price_schema_declares() -> None:
    props = _schema_props("price_radar")
    body = _fn_body(VIEWS["home"], "renderMovers")
    for key in ("risers", "fallers"):
        assert key in props and f"res.{key}" in body
    assert "res.rows" not in body, (
        "the price schema forbids `rows`; reading it is the original bug"
    )
    row_props = _schema_props("price_radar", "risers")
    used = set(re.findall(r"\br\.(\w+)", body))
    unknown = used - row_props
    assert not unknown, (
        f"renderMovers reads {sorted(unknown)} which riser rows do not carry "
        f"(they have {sorted(row_props)})"
    )


def test_idea_chips_speak_the_panel_vocabulary() -> None:
    """The panel emits hit/miss (its hit_rate counts exactly those); the JS
    once tested only the CLI's correct/incorrect, so no chip ever coloured."""
    body = _fn_body(VIEWS["home"], "renderIdeas")
    assert '"hit"' in body and '"miss"' in body


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


def test_no_raw_html_from_model_or_panel_output() -> None:
    """Chat and panels render server/model text; innerHTML on raw output is an
    injection seam. textContent/createTextNode only, except vetted literals."""
    for name, src in VIEWS.items():
        for m in re.finditer(r"\.innerHTML\s*=\s*(.+)", src):
            rhs = m.group(1).strip()
            assert rhs.startswith('"') or rhs.startswith("'") or rhs.startswith("`") and "${" not in rhs, (
                f"view {name} assigns dynamic innerHTML: {rhs[:60]}"
            )
