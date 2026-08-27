"""A YouTube source must honour the result cap, not just the fetch budget."""
import datetime as dt
import pytest
from fpl_edge.ingest.content import loaders
from fpl_edge.ingest.content.sources import Source, SourceKind

class _Resp:
    status, body = 200, "x" * 10

def test_max_items_caps_a_youtube_source(monkeypatch):
    src = Source(key="k", creator="c", kind=SourceKind.YOUTUBE,
                 url="https://youtube.com/@x/videos", policy="open") \
        if hasattr(Source, "__dataclass_fields__") else None
    made = []
    def fake(fetcher, source, *, max_videos, since):
        items = [f"item{i}" for i in range(max_videos)]
        made.append(len(items))
        return items, loaders.ProbeResult(source.key, source.url, 200, 10, len(items))
    monkeypatch.setattr(loaders, "load_youtube_source", fake)
    items, probe = loaders.load_source(None, src, max_items=3, max_videos=8)
    assert made == [8], "the fetch budget must still be spent as asked"
    assert len(items) == 3, f"result cap ignored: got {len(items)}"
    assert probe.items == 3, f"probe reports {probe.items}, not what was returned"
