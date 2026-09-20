"""Snapshots of the warehouse, and the restore that has actually been run.

A backup nobody has restored is not a backup, so the test that matters here
takes a real snapshot of a seeded warehouse, prunes, and restores it into a
fresh path through the same entry point DEPLOYMENT.md section 14 tells an
operator to type. The restored file is then promoted by the boot step that
promotes it in production, opened, and counted against the original.

The S3 half is exercised against a fake bucket built on ``httpx.MockTransport``,
so the signing, the two-step upload and the listing are covered with no network
and no credentials.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import xml.etree.ElementTree as ET

import duckdb
import httpx
import pandas as pd
import pytest

from fpl_edge.platform import boot
from fpl_edge.store import backup
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC
SEASON = "2026-27"
AS_OF = dt.datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

#: How many players the seeded warehouse holds. The restored copy has to hold
#: exactly this many or the restore moved something other than the database.
PLAYERS = 37


def seed(path) -> int:
    """A real warehouse: schema.sql, views.sql, and rows in ``dim_player``."""
    with Warehouse(path) as wh:
        wh.append("dim_player", pd.DataFrame([
            {"season": SEASON, "code": 1000 + i, "element_id": i + 1,
             "web_name": f"Player {i}", "position": (i % 4) + 1,
             "team_code": (i % 20) + 1, "as_of": AS_OF}
            for i in range(PLAYERS)
        ]))
        return int(wh.sql("SELECT count(*) AS n FROM dim_player")["n"].iloc[0])


def players_in(path) -> int:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return int(con.execute("SELECT count(*) FROM dim_player").fetchone()[0])
    finally:
        con.close()


# --------------------------------------------------------------------------
# The check that matters
# --------------------------------------------------------------------------


def test_a_snapshot_restores_into_a_fresh_warehouse_with_every_row(tmp_path,
                                                                   monkeypatch):
    """Snapshot, prune, restore, promote, count. The whole path, once.

    Every step runs through ``backup.main``, which is the command the runbook
    prints, so this test fails if the documented instruction stops working
    rather than only if the library does.
    """
    store = tmp_path / "bucket"
    db = tmp_path / "live" / "fpl.duckdb"
    db.parent.mkdir(parents=True)
    monkeypatch.setenv(backup.ENV_DIR, str(store))
    monkeypatch.setenv(backup.ENV_KEEP, "2")
    assert seed(db) == PLAYERS

    assert backup.main(["snapshot", "--db", str(db)]) == 0
    receipt = json.loads(backup.receipt_path(db).read_text())
    assert receipt["key"].startswith(backup.KEY_STEM)
    assert receipt["bytes"] > 0
    assert receipt["source_bytes"] >= receipt["bytes"]

    # The stored object is the bytes the receipt names, byte for byte.
    stored = store / receipt["key"]
    assert stored.stat().st_size == receipt["bytes"]
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == receipt["sha256"]

    assert backup.main(["verify"]) == 0
    assert backup.main(["list"]) == 0

    # A fresh path, as a restore onto a new volume would be.
    fresh = tmp_path / "restored" / "fpl.duckdb"
    fresh.parent.mkdir(parents=True)
    assert backup.main(["restore", "--db", str(fresh)]) == 0
    incoming = fresh.with_name(fresh.name + ".incoming")
    assert incoming.is_file(), "restore puts the file where boot looks for it"
    assert not fresh.exists(), "restore never writes the live database itself"

    # Boot's own promote step, the one that runs on the next deploy.
    report = boot.BootReport(data_dir=str(fresh.parent), db_path=str(fresh))
    boot.promote_incoming(fresh, report)
    assert report.promoted["players"] == PLAYERS
    assert players_in(fresh) == PLAYERS
    assert fresh.stat().st_size == db.stat().st_size


def test_retention_keeps_the_newest_and_deletes_the_rest(tmp_path, monkeypatch):
    """Four daily snapshots, a retention of two, two left and the older two
    gone. The keys sort chronologically, which is what makes this cheap."""
    store = tmp_path / "bucket"
    db = tmp_path / "fpl.duckdb"
    seed(db)
    monkeypatch.setenv(backup.ENV_DIR, str(store))
    dest = backup.configured_destination().destination

    days = [dt.datetime(2026, 9, d, 14, 30, tzinfo=UTC) for d in (1, 2, 3, 4)]
    for day in days:
        backup.make_snapshot(db, dest, keep=None, now=day)
    assert len(backup.list_snapshots(dest)) == 4

    pruned = backup.prune(dest, keep=2)
    assert len(pruned) == 2
    left = [s.key for s in backup.list_snapshots(dest)]
    assert left == [backup.snapshot_key(days[2]), backup.snapshot_key(days[3])]
    assert not list(store.glob("*20260901*")), "the manifest goes with its object"
    assert backup.latest(dest).key == backup.snapshot_key(days[3])


def test_the_command_refuses_with_a_sentence_and_not_a_traceback(tmp_path,
                                                                 monkeypatch,
                                                                 capsys):
    """An operator restoring a database reads the last line of output. A
    refusal is a sentence and an exit code, never a stack."""
    monkeypatch.setenv(backup.ENV_DIR, str(tmp_path / "empty"))
    assert backup.main(["restore", "--db", str(tmp_path / "fpl.duckdb")]) == 1
    assert "no complete snapshot" in capsys.readouterr().out

    monkeypatch.delenv(backup.ENV_DIR)
    for name in (backup.ENV_S3_ENDPOINT, backup.ENV_S3_BUCKET,
                 backup.ENV_S3_KEY_ID, backup.ENV_S3_SECRET):
        monkeypatch.delenv(name, raising=False)
    assert backup.main(["list"]) == 2
    assert backup.ENV_DIR in capsys.readouterr().out


def test_a_retention_of_zero_is_refused(tmp_path, monkeypatch):
    """Keeping nothing is never what an operator meant to type."""
    monkeypatch.setenv(backup.ENV_DIR, str(tmp_path / "bucket"))
    dest = backup.configured_destination().destination
    with pytest.raises(backup.BackupError) as caught:
        backup.prune(dest, keep=0)
    assert "keep no snapshots" in str(caught.value)


# --------------------------------------------------------------------------
# The two ways a snapshot can be a lie
# --------------------------------------------------------------------------


def test_an_object_with_no_manifest_is_incomplete_and_never_restored(tmp_path,
                                                                     monkeypatch):
    """What an upload interrupted between the two objects leaves behind."""
    store = tmp_path / "bucket"
    db = tmp_path / "fpl.duckdb"
    seed(db)
    monkeypatch.setenv(backup.ENV_DIR, str(store))
    dest = backup.configured_destination().destination
    result = backup.make_snapshot(db, dest, keep=None)
    (store / (result.key + backup.MANIFEST_SUFFIX)).unlink()

    listed = backup.list_snapshots(dest)
    assert [s.complete for s in listed] == [False]
    assert "INCOMPLETE" in listed[0].line()
    assert backup.latest(dest) is None
    with pytest.raises(backup.BackupError) as caught:
        backup.restore(dest, db, key=result.key)
    assert "did not finish" in str(caught.value)

    # An incomplete object does not survive a prune, and does not count
    # against the retention of the good snapshots beside it.
    assert backup.prune(dest, keep=7) == [result.key]


def test_a_corrupted_object_fails_verify_and_fails_the_restore(tmp_path,
                                                               monkeypatch):
    """The checksum is computed at upload and compared on the way back."""
    store = tmp_path / "bucket"
    db = tmp_path / "fpl.duckdb"
    seed(db)
    monkeypatch.setenv(backup.ENV_DIR, str(store))
    dest = backup.configured_destination().destination
    result = backup.make_snapshot(db, dest, keep=None)

    stored = store / result.key
    raw = bytearray(stored.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    stored.write_bytes(bytes(raw))

    outcome = backup.verify(dest, result.key)
    assert not outcome.ok
    assert "sha256" in outcome.detail
    with pytest.raises(backup.BackupError) as caught:
        backup.restore(dest, tmp_path / "fresh.duckdb", key=result.key)
    assert "not the one that was uploaded" in str(caught.value)
    assert not (tmp_path / "fresh.duckdb.incoming").exists()


def test_a_truncated_object_is_caught_by_its_byte_count(tmp_path, monkeypatch):
    store = tmp_path / "bucket"
    db = tmp_path / "fpl.duckdb"
    seed(db)
    monkeypatch.setenv(backup.ENV_DIR, str(store))
    dest = backup.configured_destination().destination
    result = backup.make_snapshot(db, dest, keep=None)
    stored = store / result.key
    stored.write_bytes(stored.read_bytes()[: result.bytes // 2])

    outcome = backup.verify(dest, result.key)
    assert not outcome.ok
    assert "bytes" in outcome.detail


def test_a_snapshot_of_a_schema_only_warehouse_is_refused_on_the_way_back(
        tmp_path, monkeypatch):
    """The shape a first boot leaves: a file that opens and holds nothing."""
    store = tmp_path / "bucket"
    db = tmp_path / "fpl.duckdb"
    with Warehouse(db) as wh:
        wh.sql("CHECKPOINT")
    monkeypatch.setenv(backup.ENV_DIR, str(store))
    dest = backup.configured_destination().destination
    result = backup.make_snapshot(db, dest, keep=None)
    with pytest.raises(backup.BackupError) as caught:
        backup.restore(dest, tmp_path / "fresh.duckdb", key=result.key)
    assert "dim_player" in str(caught.value)


# --------------------------------------------------------------------------
# What the snapshot holds while it runs
# --------------------------------------------------------------------------


class _WriterProbe(backup.LocalDirectory):
    """A destination that tries to open the warehouse while it uploads.

    The point of splitting ``stage_copy`` out is that the database is closed
    before a byte goes anywhere. If the upload ever ran with the warehouse
    open, this opens it as a writer and DuckDB refuses.
    """

    def __init__(self, root, db) -> None:
        super().__init__(root)
        self.db = db
        self.could_write = None

    def put_file(self, local, key, *, sha256):
        try:
            with Warehouse(self.db, lock_timeout_s=2.0) as wh:
                wh.sql("SELECT 1")
            self.could_write = True
        except Exception:  # noqa: BLE001 - the flag IS the assertion
            self.could_write = False
        super().put_file(local, key, sha256=sha256)


def test_the_warehouse_is_writable_while_the_upload_runs(tmp_path):
    """The upload holds no database handle, so no writer waits behind it."""
    db = tmp_path / "fpl.duckdb"
    seed(db)
    dest = _WriterProbe(tmp_path / "bucket", db)
    backup.make_snapshot(db, dest, keep=None)
    assert dest.could_write is True


def test_a_failed_upload_leaves_no_staging_copy(tmp_path):
    """173 MB left behind on the volume this exists to protect is its own
    outage, so the staging directory goes even when the upload raises."""
    db = tmp_path / "fpl.duckdb"
    seed(db)
    staging = tmp_path / "staging"

    class Broken(backup.LocalDirectory):
        def put_file(self, local, key, *, sha256):
            raise backup.BackupError("the bucket said no")

    with pytest.raises(backup.BackupError):
        backup.make_snapshot(db, Broken(tmp_path / "bucket"),
                             staging_dir=staging, keep=None)
    assert list(staging.iterdir()) == []


# --------------------------------------------------------------------------
# Configuration, and the absence of it
# --------------------------------------------------------------------------


def test_no_configuration_is_a_named_reason_and_not_a_destination():
    configured = backup.configured_destination(env={})
    assert configured.destination is None
    assert backup.ENV_DIR in configured.reason
    assert backup.ENV_S3_BUCKET in configured.reason


def test_a_half_configured_bucket_is_refused_rather_than_silently_local():
    """Half an S3 block means somebody meant to use a bucket. Falling back to
    a directory on a container filesystem would store the snapshots where the
    next deploy deletes them."""
    configured = backup.configured_destination(env={
        backup.ENV_S3_ENDPOINT: "https://example.invalid",
        backup.ENV_S3_BUCKET: "fpl-edge",
        backup.ENV_DIR: "/tmp/somewhere",
    })
    assert configured.destination is None
    assert backup.ENV_S3_KEY_ID in configured.reason
    assert "half configured" in configured.reason


def test_a_full_bucket_block_wins_over_a_directory():
    configured = backup.configured_destination(env={
        backup.ENV_S3_ENDPOINT: "https://example.invalid",
        backup.ENV_S3_BUCKET: "fpl-edge",
        backup.ENV_S3_KEY_ID: "id",
        backup.ENV_S3_SECRET: "secret",
        backup.ENV_S3_PREFIX: "warehouse",
        backup.ENV_DIR: "/tmp/somewhere",
    })
    assert isinstance(configured.destination, backup.S3Bucket)
    assert configured.destination.prefix == "warehouse/"


@pytest.mark.parametrize("raw,expected", [
    ("", backup.DEFAULT_KEEP), ("3", 3), ("0", backup.DEFAULT_KEEP),
    ("-2", backup.DEFAULT_KEEP), ("seven", backup.DEFAULT_KEEP),
])
def test_the_retention_reads_the_environment_and_refuses_nonsense(raw, expected):
    assert backup.configured_keep(env={backup.ENV_KEEP: raw}) == expected


# --------------------------------------------------------------------------
# The S3 destination, against a fake bucket
# --------------------------------------------------------------------------


class FakeBucket:
    """Enough of S3 to answer this module: PUT, GET, HEAD, DELETE, list, copy.

    Every request is checked for the headers SigV4 requires before it is
    answered, so an unsigned or wrongly-hashed request fails here rather than
    against a real bucket six hours later.
    """

    NS = "http://s3.amazonaws.com/doc/2006-03-01/"

    def __init__(self, name: str = "fpl-edge") -> None:
        self.name = name
        self.objects: dict[str, bytes] = {}
        self.truncate_next = False
        self.seen: list[tuple[str, str]] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        assert auth.startswith("AWS4-HMAC-SHA256 Credential="), auth
        assert "SignedHeaders=" in auth and "Signature=" in auth
        assert "x-amz-date" in request.headers
        body = request.content
        if body:
            assert (request.headers["x-amz-content-sha256"]
                    == hashlib.sha256(body).hexdigest())
        path = request.url.path
        assert path.startswith(f"/{self.name}"), path
        key = path[len(self.name) + 2:]
        self.seen.append((request.method, key))

        if request.method == "GET" and not key:
            return self._list(request)
        if request.method == "PUT":
            source = request.headers.get("x-amz-copy-source")
            if source:
                from urllib.parse import unquote
                src = unquote(source)[len(self.name) + 2:]
                self.objects[key] = self.objects[src]
                return httpx.Response(200, text="<CopyObjectResult/>")
            data = body
            if self.truncate_next:
                data = data[: len(data) // 2]
                self.truncate_next = False
            self.objects[key] = data
            return httpx.Response(200)
        if request.method in ("GET", "HEAD"):
            if key not in self.objects:
                return httpx.Response(404, text="<Error>NoSuchKey</Error>")
            data = self.objects[key]
            headers = {"content-length": str(len(data))}
            if request.method == "HEAD":
                return httpx.Response(200, headers=headers)
            return httpx.Response(200, content=data)
        if request.method == "DELETE":
            self.objects.pop(key, None)
            return httpx.Response(204)
        return httpx.Response(405)

    def _list(self, request: httpx.Request) -> httpx.Response:
        prefix = request.url.params.get("prefix", "")
        root = ET.Element(f"{{{self.NS}}}ListBucketResult")
        ET.SubElement(root, f"{{{self.NS}}}IsTruncated").text = "false"
        for key in sorted(k for k in self.objects if k.startswith(prefix)):
            node = ET.SubElement(root, f"{{{self.NS}}}Contents")
            ET.SubElement(node, f"{{{self.NS}}}Key").text = key
            ET.SubElement(node, f"{{{self.NS}}}Size").text = str(
                len(self.objects[key]))
        return httpx.Response(200, text=ET.tostring(root, encoding="unicode"))


def s3_against(fake: FakeBucket, prefix: str = "warehouse") -> backup.S3Bucket:
    return backup.S3Bucket(
        endpoint="https://bucket.example.invalid",
        bucket=fake.name, access_key_id="AKIAEXAMPLE",
        secret_access_key="s3cr3t", prefix=prefix, region="auto",
        client=httpx.Client(transport=fake.transport()),
    )


def test_the_bucket_round_trips_a_snapshot_and_prunes_it(tmp_path):
    db = tmp_path / "fpl.duckdb"
    seed(db)
    fake = FakeBucket()
    dest = s3_against(fake)

    first = backup.make_snapshot(db, dest, keep=None,
                                 now=dt.datetime(2026, 9, 1, 14, 30, tzinfo=UTC))
    second = backup.make_snapshot(db, dest, keep=None,
                                  now=dt.datetime(2026, 9, 2, 14, 30, tzinfo=UTC))

    assert f"warehouse/{first.key}" in fake.objects
    assert f"warehouse/{first.key}{backup.MANIFEST_SUFFIX}" in fake.objects
    assert not [k for k in fake.objects if k.endswith(backup.PART_SUFFIX)], (
        "the staging key is deleted once the object is in place")

    listed = [s.key for s in backup.list_snapshots(dest)]
    assert listed == [first.key, second.key]
    assert backup.verify(dest, second.key).ok

    restored = backup.restore(dest, tmp_path / "fresh.duckdb", key=first.key)
    assert restored.players == PLAYERS
    assert players_in(tmp_path / "fresh.duckdb.incoming") == PLAYERS

    assert backup.prune(dest, keep=1) == [first.key]
    assert [s.key for s in backup.list_snapshots(dest)] == [second.key]


def test_a_cut_short_upload_never_becomes_a_snapshot(tmp_path):
    """The HEAD after the upload is what catches a dropped connection, and
    the final key is only written once that check passes."""
    db = tmp_path / "fpl.duckdb"
    seed(db)
    fake = FakeBucket()
    fake.truncate_next = True
    dest = s3_against(fake)

    with pytest.raises(backup.BackupError) as caught:
        backup.make_snapshot(db, dest, keep=None)
    assert "cut short" in str(caught.value)
    assert backup.list_snapshots(dest) == []
    assert fake.objects == {}, "the staging key is cleaned up after the refusal"


def test_the_bucket_prefix_is_stripped_from_the_keys_it_reports(tmp_path):
    db = tmp_path / "fpl.duckdb"
    seed(db)
    fake = FakeBucket()
    dest = s3_against(fake, prefix="nested/path")
    result = backup.make_snapshot(db, dest, keep=None)
    assert f"nested/path/{result.key}" in fake.objects
    assert [s.key for s in backup.list_snapshots(dest)] == [result.key]


def test_an_error_from_the_bucket_names_the_key_and_the_status(tmp_path):
    db = tmp_path / "fpl.duckdb"
    seed(db)

    class Refusing(FakeBucket):
        def handle(self, request):
            if request.method == "PUT":
                return httpx.Response(403, text="<Error>AccessDenied</Error>")
            return super().handle(request)

    with pytest.raises(backup.BackupError) as caught:
        backup.make_snapshot(db, s3_against(Refusing()), keep=None)
    assert "403" in str(caught.value)
    assert backup.KEY_STEM in str(caught.value)


# --------------------------------------------------------------------------
# The scheduled task
# --------------------------------------------------------------------------


def test_the_task_is_daily_maintenance_with_a_budget():
    from fpl_edge.pipelines import health, registry

    task = registry.by_id("warehouse_backup")
    assert task is not None
    assert task.family == "maintenance"
    assert health.describe_due(task.due) == "daily 14:30 UTC"
    assert task.budget_s == registry.BACKUP_TIMEOUT_S
    assert task.token_budget is None, "a file copy spends no model tokens"


def test_the_task_records_no_source_when_nothing_is_configured(tmp_path,
                                                               monkeypatch):
    """No destination is an honest gap, not a green run over an empty bucket."""
    from fpl_edge.pipelines import registry
    from fpl_edge.pipelines.contracts import TaskContext

    monkeypatch.delenv(backup.ENV_DIR, raising=False)
    for name in (backup.ENV_S3_ENDPOINT, backup.ENV_S3_BUCKET,
                 backup.ENV_S3_KEY_ID, backup.ENV_S3_SECRET):
        monkeypatch.delenv(name, raising=False)
    db = tmp_path / "fpl.duckdb"
    seed(db)
    ctx = TaskContext(season=SEASON, gw=0, due_utc=AS_OF, deadline_utc=None,
                      now=AS_OF, db_path=db)
    result = registry.run_warehouse_backup(ctx)
    assert result.outcome == "no_source"
    assert backup.ENV_DIR in result.detail
    assert not backup.receipt_path(db).exists()


def test_the_ledger_row_carries_the_key_the_size_and_the_checksum(tmp_path,
                                                                  monkeypatch):
    """The scheduled task, run through the same seam the Pipelines tab uses.

    This spawns the real subprocess, so it also proves the argv the runbook
    prints is an argv the module accepts.
    """
    from fpl_edge.pipelines import runner

    db = tmp_path / "fpl.duckdb"
    seed(db)
    store = tmp_path / "bucket"
    monkeypatch.setenv(backup.ENV_DIR, str(store))
    monkeypatch.setenv(backup.ENV_KEEP, "7")

    outcome = runner.run_task("warehouse_backup", db_path=db, trigger="cli")
    assert outcome.result.outcome == "quiet", outcome.result.detail
    receipt = json.loads(backup.receipt_path(db).read_text())
    detail = outcome.result.detail
    assert f"key={receipt['key']}" in detail
    assert f"bytes={receipt['bytes']}" in detail
    assert f"sha256={receipt['sha256']}" in detail
    assert "elapsed_s=" in detail and "pruned=0" in detail
    assert outcome.result.ledger_written == 1

    with Warehouse(db) as wh:
        row = wh.sql(
            "SELECT status, note, rows_written FROM fetch_run "
            "WHERE pipeline = 'warehouse_backup' "
            "ORDER BY started_utc DESC LIMIT 1")
    assert len(row) == 1
    assert row["status"].iloc[0] == "ok"
    assert receipt["sha256"] in row["note"].iloc[0]
    assert int(row["rows_written"].iloc[0]) == 1


def test_a_snapshot_decompresses_to_the_exact_bytes_it_was_taken_from(tmp_path,
                                                                      monkeypatch):
    """gzip is lossless, and this is the assertion that says so about THIS
    file rather than about gzip in general."""
    db = tmp_path / "fpl.duckdb"
    seed(db)
    monkeypatch.setenv(backup.ENV_DIR, str(tmp_path / "bucket"))
    dest = backup.configured_destination().destination
    # Checkpointed first, then hashed, then snapshotted with the checkpoint
    # already done: a second CHECKPOINT rewrites the file's header and the
    # comparison would be against a file that no longer exists.
    backup.checkpoint(db)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    result = backup.make_snapshot(db, dest, keep=None, do_checkpoint=False)
    with gzip.open(tmp_path / "bucket" / result.key, "rb") as gz:
        after = hashlib.sha256(gz.read()).hexdigest()
    assert after == before
