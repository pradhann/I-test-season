"""The frontend's assumptions, checked against the backend's declarations.

There was no frontend test of any kind, and four shipped bugs prove what that
costs: a panel pinned to a layout whose renderer reads a key the panel's own
schema forbids ("No data." rendered over real data); player cards reading
`price_tenths` where the schema says `price` (fifteen cards of £NaN); an
outcome vocabulary the panel never emits (chips that never colour); and a
hardcoded deadline. These are CONTRACT tests: they parse the single-file
bundle's JS and hold it against the registered panels and schemas, which is
exactly the seam every one of those bugs lived on.
"""

from __future__ import annotations

import re
from pathlib import Path

from fpl_edge.platform import panels as panels_mod
import fpl_edge.platform.scripts  # noqa: F401 - registration is the import
from fpl_edge.platform.registry import script as get_script

BUNDLE = Path(__file__).resolve().parents[2] / "web" / "dist" / "index.html"


def _js() -> str:
    html = BUNDLE.read_text()
    m = re.search(r"<script>(.*)</script>", html, re.S)
    assert m, "no inline script in the bundle"
    return m.group(1)


def _render_map() -> dict[str, str]:
    """layout -> renderer-function name, parsed from the RENDER map literal."""
    m = re.search(r"const RENDER = \{([^}]*)\}", _js(), re.S)
    assert m, "no RENDER map in the bundle"
    return dict(re.findall(r"(\w+)\s*:\s*(\w+)", m.group(1)))


def test_every_declared_layout_has_a_renderer() -> None:
    """A layout without a renderer used to silently fall back to renderTable.

    That fallback is how the price radar rendered empty over real data. The
    fallback is now an explicit error; this test keeps the map complete so the
    error stays theoretical.
    """
    render = _render_map()
    for panel in panels_mod.PANELS:
        assert panel.layout in render, (
            f"panel {panel.id!r} declares layout {panel.layout!r}; the bundle "
            f"renders only {sorted(render)}"
        )


def test_unknown_layouts_error_rather_than_masquerade_as_tables() -> None:
    js = _js()
    assert "RENDER[p.layout]||renderTable" not in js.replace(" ", ""), (
        "the silent renderTable fallback is back; an unknown layout must "
        "surface as an error"
    )
    assert "no renderer" in js


def _schema_props(script: str, *path: str) -> set[str]:
    node = get_script(script).result_schema
    # registration wraps every schema in oneOf[real, EMPTY]; unwrap the real one
    if "oneOf" in node:
        node = node["oneOf"][0]
    for key in path:
        node = node["properties"][key]
        if node.get("type") == "array":
            node = node["items"]
    return set(node.get("properties", {}))


def _renderer_body(name: str) -> str:
    m = re.search(rf"function {name}\(res, host\)\{{(.*?)\n\}}", _js(), re.S)
    assert m, f"renderer {name} not found"
    return m.group(1)


def test_the_pitch_reads_only_fields_the_squad_schema_carries() -> None:
    """`p.price_tenths` rendered £NaN on all fifteen cards; the schema says
    `price` and forbids everything else."""
    allowed = _schema_props("squad_overview", "starters")
    body = _renderer_body("renderPitch")
    used = set(re.findall(r"\bp\.(\w+)", body))
    unknown = used - allowed
    assert not unknown, (
        f"renderPitch reads {sorted(unknown)}, which the squad player schema "
        f"does not carry (it has {sorted(allowed)}); those render as undefined"
    )


def test_the_movers_renderer_reads_what_the_price_schema_declares() -> None:
    props = _schema_props("price_radar")
    body = _renderer_body("renderMovers")
    for key in ("risers", "fallers"):
        assert key in props and f"res.{key}" in body
    assert "res.rows" not in body, (
        "the price schema forbids `rows`; reading it is the original bug"
    )


def test_idea_chips_speak_the_panel_vocabulary() -> None:
    """The panel emits hit/miss (its own hit_rate counts exactly those); the
    JS once tested only the CLI's correct/incorrect, so no chip ever coloured."""
    body = _renderer_body("renderList")
    assert '"hit"' in body and '"miss"' in body


def test_the_deadline_is_fetched_never_hardcoded() -> None:
    js = _js()
    assert not re.search(r'new Date\("\d{4}-\d{2}-\d{2}T', js), (
        "a hardcoded deadline literal is back; it expires and then counts up "
        "forever"
    )
    assert "/api/deadline" in js


def test_the_grid_caption_does_not_claim_difficulty_it_cannot_have() -> None:
    """The fixture schema has no difficulty field today; the caption must not
    describe the colouring as live. When the field arrives, the honest caption
    flips on the data, not on the prose."""
    body = _renderer_body("renderGrid")
    fixture_props = _schema_props("fixture_ticker", "teams", "fixtures", "opponents")
    if "difficulty" not in fixture_props:
        assert "No difficulty rating is wired yet" in body


def test_sorting_rebuilds_the_table_not_the_whole_panel() -> None:
    """host.innerHTML="" on a header click deleted the provenance footer."""
    js = _js()
    assert 'host.innerHTML=""; renderTable' not in js
    assert "replaceWith(buildTable" in js


def test_the_clock_never_hardcodes_a_gameweek_label() -> None:
    """The countdown once said "GW1 ... Fri 21 Aug" forever, from prose baked
    into tick() -- outliving the fetched date it counted down to."""
    js = _js()
    assert "GW1 deadline" not in js
    assert "DEADLINE_GW" in js
