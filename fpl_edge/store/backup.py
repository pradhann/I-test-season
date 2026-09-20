"""Snapshots of the warehouse file, and the restore that puts one back.

Four seasons of ingest live in one DuckDB file on one Railway volume. A volume
is not a backup: it is one disk, attached to one service, with no history. A
`rm`, a bad migration, a corrupted WAL replay or a deleted service takes the
whole thing, and every number the engine serves with it.

What a snapshot is
------------------

One `CHECKPOINT`, so the write-ahead log is folded into the file and nothing is
outstanding; one file copy, measured at 0.11s for the live 173,551,616-byte
warehouse on the Railway volume's local SSD; one gzip pass, measured at 4.1s
for 35,403,474 bytes out, a ratio of 0.204; one upload. The copy is what makes
the snapshot consistent: DuckDB's file is a complete database at copy time, and
`Warehouse.read_copy` has relied on that property since the first heavy panel.
A round trip over the live file was checked before this module was written, and
the decompressed copy is byte-identical to the source.

gzip level 6 rather than 1 or 9. Level 1 gives 39,372,916 bytes in 1.2s and
level 9 gives 35,099,541 in 26.4s, so 6 buys 10 percent over level 1 for three
seconds and level 9 buys 1 percent more for another twenty-two. The compression
happens outside the writer lease, so those seconds cost the scheduler nothing.

What a destination is
---------------------

:class:`Destination` is an object store with six operations and no opinions.
Two implementations:

* :class:`LocalDirectory` writes files into a directory. It needs no cloud,
  the tests use it, and it is what a Mac uses to keep snapshots on an external
  disk.
* :class:`S3Bucket` speaks S3 over httpx with SigV4 signed by hand, which is
  what Railway's `railway bucket` provides. No S3 client library is installed
  in this repo and adding one to write four requests would be a dependency for
  `hashlib` and `hmac` work the standard library already does.

Neither one half-writes. A data object is uploaded under a `.part` key and
moved into place, and the manifest that names it is written last, so a
snapshot only exists once both are there. :func:`list_snapshots` reports a data
object with no manifest as incomplete rather than counting it.

What is written per snapshot
----------------------------

Two objects. `fpl-duckdb-<utc>.duckdb.gz` is the compressed database, and
`fpl-duckdb-<utc>.duckdb.gz.json` is a manifest carrying the sha256 of the
compressed bytes, both byte counts and the instant. The checksum is computed
while the bytes are being compressed, so it covers exactly what was uploaded,
and :func:`verify` recomputes it from the stored object on the way back.

The restore half
----------------

``restore`` downloads a snapshot, verifies its size and checksum, decompresses
it, checks the result opens as a DuckDB warehouse with rows in `dim_player`,
and puts it at ``<db>.incoming``. From there it is
:func:`fpl_edge.platform.boot.promote_incoming`'s problem, which validates it
again and renames it into place before any connection is opened. That path was
built for the first seed and is already tested; this module feeds it rather
than inventing a second way to replace a live database.

No configuration means no snapshot, said out loud
-------------------------------------------------

With no destination in the environment, :func:`configured_destination` returns
the reason and the scheduled task records ``no_source`` with it. A backup task
that writes nothing and reports success is worse than no backup task, because
it also removes the operator's reason to look.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import hmac
import json
import os
import shutil
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

UTC = dt.UTC

#: gzip level. See the module docstring for the three measurements.
COMPRESS_LEVEL = 6

#: Read size for copying, hashing and compressing. 4 MiB keeps the 173 MB
#: warehouse at 42 reads rather than a million.
CHUNK = 4 << 20

#: How many daily snapshots are kept when nothing says otherwise. Seven days
#: covers a bad write noticed the following weekend, at 35 MB each.
DEFAULT_KEEP = 7

#: Key prefix and suffix for a snapshot's data object.
KEY_STEM = "fpl-duckdb-"
KEY_SUFFIX = ".duckdb.gz"

#: The manifest sits beside the data object under this suffix, and is written
#: after it. A data object without one is an upload that did not finish.
MANIFEST_SUFFIX = ".json"

#: Suffix for the key a destination uploads to before moving into place.
PART_SUFFIX = ".part"

#: Environment names. One directory, or the five S3 values, or nothing.
ENV_DIR = "FPL_EDGE_BACKUP_DIR"
ENV_S3_ENDPOINT = "FPL_EDGE_BACKUP_S3_ENDPOINT"
ENV_S3_BUCKET = "FPL_EDGE_BACKUP_S3_BUCKET"
ENV_S3_KEY_ID = "FPL_EDGE_BACKUP_S3_ACCESS_KEY_ID"
ENV_S3_SECRET = "FPL_EDGE_BACKUP_S3_SECRET_ACCESS_KEY"
ENV_S3_PREFIX = "FPL_EDGE_BACKUP_S3_PREFIX"
ENV_S3_REGION = "FPL_EDGE_BACKUP_S3_REGION"
ENV_KEEP = "FPL_EDGE_BACKUP_KEEP"

#: What an S3-compatible endpoint that does not name a region gets. Cloudflare
#: R2 uses this literal value and MinIO ignores it.
DEFAULT_REGION = "auto"


class BackupError(RuntimeError):
    """A snapshot or restore that failed, with the key or path in the message."""


# --------------------------------------------------------------------------
# Destinations
# --------------------------------------------------------------------------


class Destination:
    """An object store: put, get, list, delete, and two of those for bytes.

    Every method raises :class:`BackupError` naming the key on failure, and
    ``put_file`` is atomic by construction: a reader that lists the store
    never sees a key holding half an upload.
    """

    #: What this destination is, for the ledger row and the panel.
    kind: str = "none"

    def describe(self) -> str:
        raise NotImplementedError

    def put_file(self, local: Path, key: str, *, sha256: str) -> None:
        raise NotImplementedError

    def get_file(self, key: str, local: Path) -> None:
        raise NotImplementedError

    def put_bytes(self, key: str, data: bytes) -> None:
        raise NotImplementedError

    def get_bytes(self, key: str) -> bytes | None:
        raise NotImplementedError

    def list_keys(self) -> list[str]:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


class LocalDirectory(Destination):
    """Snapshots as files in a directory. No cloud, no credentials.

    The directory is created on first use. ``put_file`` writes the neighbouring
    ``.part`` name and renames, so the final name appears whole or not at all
    even if the process is killed mid-copy.
    """

    kind = "local"

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def describe(self) -> str:
        return f"local directory {self.root}"

    def _path(self, key: str) -> Path:
        if "/" in key or key in ("", ".", ".."):
            raise BackupError(f"a snapshot key may not contain a path: {key!r}")
        return self.root / key

    def put_file(self, local: Path, key: str, *, sha256: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._path(key)
        part = target.with_name(target.name + PART_SUFFIX)
        try:
            shutil.copyfile(local, part)
            part.replace(target)
        except OSError as exc:
            part.unlink(missing_ok=True)
            raise BackupError(f"could not write {target}: {exc}") from exc

    def get_file(self, key: str, local: Path) -> None:
        src = self._path(key)
        if not src.is_file():
            raise BackupError(f"no snapshot at {src}")
        local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, local)

    def put_bytes(self, key: str, data: bytes) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._path(key)
        part = target.with_name(target.name + PART_SUFFIX)
        part.write_bytes(data)
        part.replace(target)

    def get_bytes(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def list_keys(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir()
                      if p.is_file() and not p.name.endswith(PART_SUFFIX))

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3Bucket(Destination):
    """S3-compatible object storage over httpx, signed with SigV4 by hand.

    Path-style addressing (``<endpoint>/<bucket>/<key>``), which MinIO, R2 and
    AWS all accept and which needs no DNS for the bucket name.

    ``put_file`` uploads to ``<key>.part``, reads the length back with a HEAD,
    server-side copies to the final key and deletes the part. The HEAD is the
    check that a connection dropped at 90 percent does not become a snapshot,
    and the copy is what makes the final key appear in one step.

    ``client`` is injectable so the tests drive this class against a fake
    bucket with no network.
    """

    kind = "s3"

    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        prefix: str = "",
        region: str = DEFAULT_REGION,
        client: Any | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.bucket = bucket
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.region = region or DEFAULT_REGION
        self._client = client

    def describe(self) -> str:
        where = f"{self.endpoint}/{self.bucket}"
        return f"s3 bucket {where}/{self.prefix}" if self.prefix else f"s3 bucket {where}"

    # -- plumbing ---------------------------------------------------------

    def client(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(
                timeout=httpx.Timeout(connect=10.0, read=120.0,
                                      write=600.0, pool=10.0),
            )
        return self._client

    def _canonical_path(self, key: str = "") -> str:
        path = f"/{self.bucket}"
        if key:
            path += "/" + urllib.parse.quote(key, safe="/~")
        return path

    @staticmethod
    def _canonical_query(query: dict[str, str]) -> str:
        """The query string, encoded once, sorted, as SigV4 defines it.

        The same string goes into the signature and into the URL. Letting the
        HTTP client encode the parameters a second time is how a signature
        that is correct on paper fails against a real bucket, because the two
        encoders disagree about which characters are safe.
        """
        return "&".join(
            f"{urllib.parse.quote(k, safe='~')}={urllib.parse.quote(v, safe='~')}"
            for k, v in sorted(query.items()))

    def _url(self, key: str = "", query: dict[str, str] | None = None) -> str:
        url = self.endpoint + self._canonical_path(key)
        encoded = self._canonical_query(query or {})
        return f"{url}?{encoded}" if encoded else url

    def _request(
        self,
        method: str,
        key: str = "",
        *,
        query: dict[str, str] | None = None,
        payload_sha256: str,
        body: bytes | IO[bytes] | None = None,
        content_length: int | None = None,
        extra_headers: dict[str, str] | None = None,
        stream_to: Path | None = None,
        allow_404: bool = False,
    ):
        """One signed request. Returns the response, or None on an allowed 404."""
        import httpx

        headers = self._signed_headers(
            method, self._canonical_path(key), query or {},
            payload_sha256, extra_headers or {},
        )
        if content_length is not None:
            headers["content-length"] = str(content_length)
        url = self._url(key, query)
        client = self.client()
        try:
            if stream_to is not None:
                with client.stream(method, url, headers=headers) as resp:
                    if resp.status_code == 404 and allow_404:
                        return None
                    if resp.status_code >= 400:
                        resp.read()
                        raise BackupError(
                            f"{method} {key or self.bucket} returned "
                            f"{resp.status_code}: {resp.text[:300]}")
                    stream_to.parent.mkdir(parents=True, exist_ok=True)
                    with open(stream_to, "wb") as out:
                        out.writelines(resp.iter_bytes(CHUNK))
                    return resp
            resp = client.request(method, url, headers=headers, content=body)
        except httpx.HTTPError as exc:
            raise BackupError(
                f"{method} {key or self.bucket} failed against "
                f"{self.endpoint}: {type(exc).__name__}: {exc}") from exc
        if resp.status_code == 404 and allow_404:
            return None
        if resp.status_code >= 400:
            raise BackupError(
                f"{method} {key or self.bucket} returned {resp.status_code}: "
                f"{resp.text[:300]}")
        return resp

    def _signed_headers(
        self,
        method: str,
        canonical_path: str,
        query: dict[str, str],
        payload_sha256: str,
        extra: dict[str, str],
    ) -> dict[str, str]:
        """AWS Signature Version 4, the four steps of the published algorithm.

        The payload hash is the sha256 this module already computed for the
        object, so the bucket verifies the same bytes the manifest names.
        """
        now = dt.datetime.now(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        host = urllib.parse.urlsplit(self.endpoint).netloc

        headers = {k.lower(): v for k, v in extra.items()}
        headers["host"] = host
        headers["x-amz-content-sha256"] = payload_sha256
        headers["x-amz-date"] = amz_date

        signed_names = sorted(headers)
        canonical_headers = "".join(
            f"{name}:{headers[name].strip()}\n" for name in signed_names)
        signed_headers = ";".join(signed_names)
        canonical_query = self._canonical_query(query)
        # canonical_headers already ends with a newline, so the blank line
        # the signing spec puts before the signed-header list is the join of
        # that newline and this one.
        canonical_request = (
            f"{method}\n{canonical_path}\n{canonical_query}\n"
            f"{canonical_headers}\n{signed_headers}\n{payload_sha256}")

        scope = f"{datestamp}/{self.region}/s3/aws4_request"
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ])

        def sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        signing_key = sign(
            sign(sign(sign(f"AWS4{self.secret_access_key}".encode(), datestamp),
                      self.region), "s3"), "aws4_request")
        signature = hmac.new(signing_key, string_to_sign.encode(),
                             hashlib.sha256).hexdigest()
        headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}")
        return headers

    # -- the interface ----------------------------------------------------

    def put_file(self, local: Path, key: str, *, sha256: str) -> None:
        size = local.stat().st_size
        part = self.prefix + key + PART_SUFFIX
        final = self.prefix + key
        with open(local, "rb") as body:
            self._request("PUT", part, payload_sha256=sha256, body=body,
                          content_length=size)
        head = self._request("HEAD", part, payload_sha256=_EMPTY_SHA256)
        stored = int(head.headers.get("content-length", -1))
        if stored != size:
            self.delete_absolute(part)
            raise BackupError(
                f"{part} arrived as {stored} bytes against {size} sent, so the "
                f"upload was cut short. Nothing was promoted to {final}.")
        self._request(
            "PUT", final, payload_sha256=_EMPTY_SHA256,
            extra_headers={"x-amz-copy-source":
                           f"/{self.bucket}/{urllib.parse.quote(part, safe='/~')}"},
        )
        self.delete_absolute(part)

    def get_file(self, key: str, local: Path) -> None:
        self._request("GET", self.prefix + key, payload_sha256=_EMPTY_SHA256,
                      stream_to=local)

    def put_bytes(self, key: str, data: bytes) -> None:
        self._request("PUT", self.prefix + key,
                      payload_sha256=hashlib.sha256(data).hexdigest(),
                      body=data)

    def get_bytes(self, key: str) -> bytes | None:
        resp = self._request("GET", self.prefix + key,
                             payload_sha256=_EMPTY_SHA256, allow_404=True)
        return None if resp is None else resp.content

    def list_keys(self) -> list[str]:
        """Every key under the prefix, with the prefix stripped.

        ListObjectsV2, followed while the response says it is truncated, so a
        bucket holding more than one page of keys still prunes correctly.
        """
        import xml.etree.ElementTree as ET

        ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
        out: list[str] = []
        token: str | None = None
        while True:
            query = {"list-type": "2"}
            if self.prefix:
                query["prefix"] = self.prefix
            if token:
                query["continuation-token"] = token
            resp = self._request("GET", "", query=query,
                                 payload_sha256=_EMPTY_SHA256)
            root = ET.fromstring(resp.text)
            for node in root.findall(f"{ns}Contents"):
                name = (node.findtext(f"{ns}Key") or "").removeprefix(self.prefix)
                if name and not name.endswith(PART_SUFFIX):
                    out.append(name)
            if (root.findtext(f"{ns}IsTruncated") or "").lower() != "true":
                break
            token = root.findtext(f"{ns}NextContinuationToken")
            if not token:
                break
        return sorted(out)

    def delete(self, key: str) -> None:
        self.delete_absolute(self.prefix + key)

    def delete_absolute(self, key: str) -> None:
        self._request("DELETE", key, payload_sha256=_EMPTY_SHA256,
                      allow_404=True)


#: sha256 of no bytes at all, which is what SigV4 wants for a request with no
#: body of its own (GET, HEAD, DELETE, and the copy PUT).
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Configured:
    """The destination the environment asks for, or why there is none."""

    destination: Destination | None
    reason: str


def configured_destination(env: dict[str, str] | None = None) -> Configured:
    """Read one destination out of the environment.

    S3 wins when its four required values are all set, because a deployment
    that has a bucket means to use it. A partly-filled S3 block is a named
    failure rather than a quiet fall back to a directory on an ephemeral
    container filesystem, which is where a silent backup would go to die.
    """
    env = dict(os.environ if env is None else env)
    s3_names = (ENV_S3_ENDPOINT, ENV_S3_BUCKET, ENV_S3_KEY_ID, ENV_S3_SECRET)
    present = [n for n in s3_names if env.get(n, "").strip()]
    if len(present) == len(s3_names):
        return Configured(S3Bucket(
            endpoint=env[ENV_S3_ENDPOINT].strip(),
            bucket=env[ENV_S3_BUCKET].strip(),
            access_key_id=env[ENV_S3_KEY_ID].strip(),
            secret_access_key=env[ENV_S3_SECRET].strip(),
            prefix=env.get(ENV_S3_PREFIX, "").strip(),
            region=env.get(ENV_S3_REGION, "").strip() or DEFAULT_REGION,
        ), "")
    if present:
        missing = [n for n in s3_names if n not in present]
        return Configured(None, (
            f"the S3 backup destination is half configured: {', '.join(present)} "
            f"set, {', '.join(missing)} missing. Nothing was uploaded."))
    directory = env.get(ENV_DIR, "").strip()
    if directory:
        return Configured(LocalDirectory(directory), "")
    return Configured(None, (
        f"no backup destination is configured: set {ENV_DIR} for a directory, "
        f"or {ENV_S3_ENDPOINT}, {ENV_S3_BUCKET}, {ENV_S3_KEY_ID} and "
        f"{ENV_S3_SECRET} for object storage. Nothing was uploaded."))


def configured_keep(env: dict[str, str] | None = None) -> int:
    """How many snapshots to keep, from the environment or the default."""
    env = dict(os.environ if env is None else env)
    raw = env.get(ENV_KEEP, "").strip()
    if not raw:
        return DEFAULT_KEEP
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_KEEP
    return value if value >= 1 else DEFAULT_KEEP


# --------------------------------------------------------------------------
# Snapshots
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredSnapshot:
    """One snapshot as the destination holds it.

    ``complete`` is False for a data object whose manifest is absent or
    unreadable, which is what an upload interrupted between the two objects
    leaves. Such a key is listed, never restored, and pruned last.
    """

    key: str
    bytes: int | None
    sha256: str | None
    source_bytes: int | None
    created_utc: str | None
    complete: bool

    def line(self) -> str:
        """One row for the operator, in the units the manifest stores."""
        if not self.complete:
            return f"{self.key}  INCOMPLETE, no manifest"
        mb = (self.bytes or 0) / 1_000_000
        src = (self.source_bytes or 0) / 1_000_000
        return (f"{self.key}  {self.bytes} bytes ({mb:.1f} MB compressed, "
                f"{src:.1f} MB warehouse)  {self.created_utc}  "
                f"sha256 {(self.sha256 or '')[:16]}")


@dataclass(frozen=True)
class SnapshotResult:
    """What one run of :func:`make_snapshot` did."""

    key: str
    bytes: int
    sha256: str
    source_bytes: int
    checkpoint_s: float
    elapsed_s: float
    pruned: list[str]
    destination: str

    def detail(self) -> str:
        """The ledger note: the key, the size, the checksum, the clock."""
        return (f"key={self.key} bytes={self.bytes} sha256={self.sha256} "
                f"source_bytes={self.source_bytes} "
                f"checkpoint_s={self.checkpoint_s:.2f} "
                f"elapsed_s={self.elapsed_s:.1f} pruned={len(self.pruned)} "
                f"destination={self.destination}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "source_bytes": self.source_bytes,
            "checkpoint_s": round(self.checkpoint_s, 3),
            "elapsed_s": round(self.elapsed_s, 3),
            "pruned": list(self.pruned),
            "destination": self.destination,
        }


#: The receipt one snapshot leaves beside the warehouse. The scheduled task
#: reads its ledger numbers out of this file rather than out of the
#: subprocess's stdout, the same seam ``briefing_intel.json`` gives its task:
#: the artefact is what was actually written, and a stdout parser is a second
#: interface that can drift from it.
RECEIPT_NAME = "warehouse_backup.json"


def receipt_path(db_path: Path | str) -> Path:
    """Where the last snapshot's receipt sits, given the warehouse path."""
    return Path(db_path).parent / RECEIPT_NAME


def snapshot_key(now: dt.datetime | None = None) -> str:
    """The key one snapshot takes. Lexical order is chronological order."""
    now = (now or dt.datetime.now(UTC)).astimezone(UTC)
    return f"{KEY_STEM}{now.strftime('%Y%m%dT%H%M%SZ')}{KEY_SUFFIX}"


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checkpoint(db_path: Path | str) -> None:
    """Fold the write-ahead log into the file, so a copy is the whole database.

    This is the one step of a snapshot that opens the warehouse as a writer,
    and it is what makes the copy below safe to take while the service is
    running.
    """
    from fpl_edge.store.warehouse import Warehouse

    with Warehouse(db_path, lock_timeout_s=180.0) as wh:
        wh.sql("CHECKPOINT")


def stage_copy(db_path: Path | str, into: Path,
               *, do_checkpoint: bool = True) -> tuple[Path, float]:
    """Checkpoint and copy the warehouse. Returns (the copy, seconds).

    THE phase that holds the database. Everything after it (compression, the
    upload, the retention pass) runs with no connection open and no lock held,
    which is what keeps a slow bucket from stalling every writer behind it.
    Measured on the live file: 0.11s for 173,551,616 bytes, against 4.1s to
    compress and however long the network takes.
    """
    db_path = Path(db_path)
    started = time.monotonic()
    if do_checkpoint:
        checkpoint(db_path)
    into.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(db_path, into)
    return into, time.monotonic() - started


class _HashingWriter:
    """A file object that hashes and counts everything on its way to disk."""

    def __init__(self, handle: IO[bytes]) -> None:
        self._handle = handle
        self.digest = hashlib.sha256()
        self.written = 0

    def write(self, data: bytes) -> int:
        self.digest.update(data)
        self.written += len(data)
        return self._handle.write(data)

    def flush(self) -> None:
        self._handle.flush()


def compress_to(src: Path, dst: Path) -> tuple[int, str]:
    """gzip ``src`` into ``dst``. Returns (compressed bytes, sha256 of them).

    The checksum is taken from the bytes on their way to the file, so it
    describes exactly what will be uploaded rather than a later re-read that
    could differ.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as raw, open(dst, "wb") as out:
        tee = _HashingWriter(out)
        # mtime=0 so two snapshots of an unchanged file compress identically,
        # which makes a stored snapshot reproducible from its source.
        with gzip.GzipFile(fileobj=tee, mode="wb",
                           compresslevel=COMPRESS_LEVEL, mtime=0) as gz:
            for chunk in iter(lambda: raw.read(CHUNK), b""):
                gz.write(chunk)
        return tee.written, tee.digest.hexdigest()


def make_snapshot(
    db_path: Path | str,
    destination: Destination,
    *,
    staging_dir: Path | str | None = None,
    key: str | None = None,
    keep: int | None = DEFAULT_KEEP,
    do_checkpoint: bool = True,
    now: dt.datetime | None = None,
) -> SnapshotResult:
    """Checkpoint, copy, compress, upload, prune. One snapshot.

    The database is open for :func:`stage_copy` and for nothing else here. By
    the time the bytes go over the network the warehouse is closed and
    writable again, so an upload that takes a minute costs the scheduler a
    minute of its own time and costs every other writer nothing.

    The staging copy is deleted whatever happens, including on an upload that
    raises, so a failed snapshot never leaves 173 MB on the volume it is
    supposed to be protecting.
    """
    db_path = Path(db_path)
    if not db_path.is_file():
        raise BackupError(f"no warehouse at {db_path}, so there is nothing to copy")
    key = key or snapshot_key(now)
    started = time.monotonic()
    staging = Path(staging_dir) if staging_dir else Path(
        tempfile.mkdtemp(prefix="fpl-backup-"))
    staging.mkdir(parents=True, exist_ok=True)
    own_staging = staging_dir is None
    raw_copy = staging / db_path.name
    archive = staging / key
    try:
        _, checkpoint_s = stage_copy(db_path, raw_copy,
                                     do_checkpoint=do_checkpoint)
        source_bytes = raw_copy.stat().st_size
        size, digest = compress_to(raw_copy, archive)
        raw_copy.unlink(missing_ok=True)
        destination.put_file(archive, key, sha256=digest)
        destination.put_bytes(key + MANIFEST_SUFFIX, json.dumps({
            "key": key,
            "bytes": size,
            "sha256": digest,
            "source_bytes": source_bytes,
            "source_path": str(db_path),
            "created_utc": (now or dt.datetime.now(UTC)).astimezone(UTC).isoformat(),
            "compresslevel": COMPRESS_LEVEL,
        }, indent=2).encode())
    finally:
        raw_copy.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
        if own_staging:
            shutil.rmtree(staging, ignore_errors=True)
    pruned = prune(destination, keep=keep) if keep else []
    return SnapshotResult(
        key=key, bytes=size, sha256=digest, source_bytes=source_bytes,
        checkpoint_s=checkpoint_s, elapsed_s=time.monotonic() - started,
        pruned=pruned, destination=destination.describe(),
    )


def list_snapshots(destination: Destination) -> list[StoredSnapshot]:
    """Every snapshot in the destination, oldest first.

    A data object with no readable manifest is returned with ``complete``
    False rather than dropped, because an operator looking for a backup has to
    be told that something is there and is not usable.
    """
    keys = [k for k in destination.list_keys()
            if k.startswith(KEY_STEM) and k.endswith(KEY_SUFFIX)]
    out: list[StoredSnapshot] = []
    for key in sorted(keys):
        raw = destination.get_bytes(key + MANIFEST_SUFFIX)
        manifest: dict[str, Any] | None = None
        if raw:
            try:
                manifest = json.loads(raw.decode())
            except (ValueError, UnicodeDecodeError):
                manifest = None
        if not isinstance(manifest, dict) or not manifest.get("sha256"):
            out.append(StoredSnapshot(key=key, bytes=None, sha256=None,
                                      source_bytes=None, created_utc=None,
                                      complete=False))
            continue
        out.append(StoredSnapshot(
            key=key,
            bytes=int(manifest.get("bytes") or 0),
            sha256=str(manifest["sha256"]),
            source_bytes=int(manifest.get("source_bytes") or 0),
            created_utc=manifest.get("created_utc"),
            complete=True,
        ))
    return out


def latest(destination: Destination) -> StoredSnapshot | None:
    """The newest complete snapshot, or None."""
    complete = [s for s in list_snapshots(destination) if s.complete]
    return complete[-1] if complete else None


def prune(destination: Destination, *, keep: int = DEFAULT_KEEP) -> list[str]:
    """Delete all but the newest ``keep`` snapshots. Returns what went.

    Incomplete snapshots are deleted first and do not count against the
    retention, so an interrupted upload cannot push a good snapshot out.
    """
    if keep < 1:
        raise BackupError(f"a retention of {keep} would keep no snapshots at all")
    snapshots = list_snapshots(destination)
    doomed = [s.key for s in snapshots if not s.complete]
    complete = [s for s in snapshots if s.complete]
    if len(complete) > keep:
        doomed += [s.key for s in complete[:len(complete) - keep]]
    for key in doomed:
        destination.delete(key)
        destination.delete(key + MANIFEST_SUFFIX)
    return sorted(doomed)


@dataclass(frozen=True)
class VerifyResult:
    """What a stored snapshot turned out to be when it was read back."""

    key: str
    ok: bool
    detail: str
    bytes: int | None = None
    sha256: str | None = None


def verify(destination: Destination, key: str,
           *, workdir: Path | str | None = None) -> VerifyResult:
    """Download one snapshot and check its bytes against its manifest.

    Size first, then the sha256 recomputed from the downloaded object. The
    downloaded copy is deleted before this returns; verifying is a question
    about the destination, not a way to get the file.
    """
    snapshots = {s.key: s for s in list_snapshots(destination)}
    stored = snapshots.get(key)
    if stored is None:
        return VerifyResult(key=key, ok=False,
                            detail=f"{key} is not in {destination.describe()}")
    if not stored.complete:
        return VerifyResult(key=key, ok=False, detail=(
            f"{key} has no manifest beside it, so it is an upload that did "
            f"not finish and there is nothing to check it against"))
    scratch = Path(workdir) if workdir else Path(
        tempfile.mkdtemp(prefix="fpl-verify-"))
    scratch.mkdir(parents=True, exist_ok=True)
    local = scratch / key
    try:
        destination.get_file(key, local)
        size = local.stat().st_size
        if size != stored.bytes:
            return VerifyResult(key=key, ok=False, bytes=size, detail=(
                f"{key} read back as {size} bytes against {stored.bytes} in "
                f"the manifest"))
        digest = _sha256_of(local)
        if digest != stored.sha256:
            return VerifyResult(key=key, ok=False, bytes=size, sha256=digest,
                                detail=(
                f"{key} read back with sha256 {digest} against "
                f"{stored.sha256} in the manifest"))
        return VerifyResult(key=key, ok=True, bytes=size, sha256=digest,
                            detail=f"{key} matches its manifest: {size} bytes")
    finally:
        local.unlink(missing_ok=True)
        if workdir is None:
            shutil.rmtree(scratch, ignore_errors=True)


# --------------------------------------------------------------------------
# Restore
# --------------------------------------------------------------------------


def _validate_warehouse_file(path: Path) -> int:
    """Open a file as a DuckDB warehouse and count ``dim_player``.

    The same probe ``boot.promote_incoming`` runs, repeated here so a bad
    restore is caught on the machine that did the download rather than on the
    next boot. Reading it twice costs a second and saves a restart.
    """
    import duckdb

    try:
        con = duckdb.connect(str(path), read_only=True)
        try:
            return int(con.execute("SELECT count(*) FROM dim_player").fetchone()[0])
        finally:
            con.close()
    except Exception as exc:  # re-raised as a backup failure with the path
        raise BackupError(
            f"{path} did not open as a DuckDB warehouse: "
            f"{type(exc).__name__}: {exc}") from exc


@dataclass(frozen=True)
class RestoreResult:
    """Where the restored warehouse was put, and what was in it."""

    key: str
    incoming_path: str
    bytes: int
    players: int
    sha256: str


def restore(
    destination: Destination,
    db_path: Path | str,
    *,
    key: str | None = None,
    workdir: Path | str | None = None,
) -> RestoreResult:
    """Put a stored snapshot at ``<db>.incoming`` for boot to promote.

    Download, check the size and the checksum against the manifest,
    decompress, open the result and count ``dim_player``, then rename into the
    staging name. The live database is never touched by this function: the
    atomic replacement is boot's, before it opens a connection, which is the
    one moment in the service's life when nothing holds the file.

    A snapshot that fails any check leaves no ``.incoming`` file at all, so a
    redeploy that follows a failed restore is an ordinary restart.
    """
    db_path = Path(db_path)
    if key is None:
        newest = latest(destination)
        if newest is None:
            raise BackupError(
                f"{destination.describe()} holds no complete snapshot, so "
                f"there is nothing to restore")
        key = newest.key
    stored = {s.key: s for s in list_snapshots(destination)}.get(key)
    if stored is None:
        raise BackupError(f"{key} is not in {destination.describe()}")
    if not stored.complete:
        raise BackupError(
            f"{key} has no manifest, so it is an upload that did not finish "
            f"and it will not be restored")

    scratch = Path(workdir) if workdir else Path(
        tempfile.mkdtemp(prefix="fpl-restore-"))
    scratch.mkdir(parents=True, exist_ok=True)
    archive = scratch / key
    expanded = scratch / (db_path.name + ".expanded")
    try:
        destination.get_file(key, archive)
        size = archive.stat().st_size
        if size != stored.bytes:
            raise BackupError(
                f"{key} downloaded as {size} bytes against {stored.bytes} in "
                f"the manifest, so the download was cut short")
        digest = _sha256_of(archive)
        if digest != stored.sha256:
            raise BackupError(
                f"{key} downloaded with sha256 {digest} against "
                f"{stored.sha256} in the manifest, so the stored object is "
                f"not the one that was uploaded")
        with gzip.open(archive, "rb") as gz, open(expanded, "wb") as out:
            shutil.copyfileobj(gz, out, CHUNK)
        players = _validate_warehouse_file(expanded)
        if players <= 0:
            raise BackupError(
                f"{key} decompressed to a warehouse with no rows in "
                f"dim_player, so it is schema-only and will not be restored")
        incoming = db_path.with_name(db_path.name + ".incoming")
        incoming.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(expanded), str(incoming))
        return RestoreResult(
            key=key, incoming_path=str(incoming),
            bytes=incoming.stat().st_size, players=players, sha256=digest,
        )
    finally:
        archive.unlink(missing_ok=True)
        expanded.unlink(missing_ok=True)
        if workdir is None:
            shutil.rmtree(scratch, ignore_errors=True)


# --------------------------------------------------------------------------
# The operator's command
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """``python -m fpl_edge.store.backup <list|snapshot|verify|prune|restore>``.

    The same five verbs DEPLOYMENT.md section 14 tells an operator to type, so
    the runbook and the code cannot describe two different tools.

    Exit 0 is the work done, 1 is a refusal with the reason on one line, and 2
    is a destination that is not configured. Every refusal this module raises
    is caught here and printed as a sentence, because the operator reading it
    is restoring a database and a stack trace is not an instruction.
    """
    try:
        return _dispatch(argv)
    except BackupError as exc:
        print(str(exc))
        return 1


def _dispatch(argv: list[str] | None) -> int:
    import argparse

    from fpl_edge.store.warehouse import DEFAULT_DB

    ap = argparse.ArgumentParser(
        prog="python -m fpl_edge.store.backup",
        description="Snapshot the warehouse, list what is stored, restore one.")
    ap.add_argument("action",
                    choices=["list", "snapshot", "verify", "prune", "restore"])
    ap.add_argument("--db", default=str(DEFAULT_DB),
                    help="the warehouse to snapshot, or to restore beside")
    ap.add_argument("--key", default=None,
                    help="which snapshot; the newest one when omitted")
    ap.add_argument("--keep", type=int, default=None,
                    help=f"how many snapshots to keep (default {DEFAULT_KEEP})")
    args = ap.parse_args(argv)

    configured = configured_destination()
    if configured.destination is None:
        print(configured.reason)
        return 2
    dest = configured.destination
    keep = args.keep if args.keep is not None else configured_keep()

    if args.action == "list":
        snapshots = list_snapshots(dest)
        print(f"{len(snapshots)} in {dest.describe()}")
        for snapshot in snapshots:
            print("  " + snapshot.line())
        return 0
    if args.action == "snapshot":
        result = make_snapshot(Path(args.db), dest, keep=keep)
        receipt = receipt_path(args.db)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(result.to_dict(), indent=2))
        print(result.detail())
        return 0
    if args.action == "verify":
        key = args.key
        if key is None:
            newest = latest(dest)
            if newest is None:
                print(f"{dest.describe()} holds no complete snapshot")
                return 2
            key = newest.key
        outcome = verify(dest, key)
        print(outcome.detail)
        return 0 if outcome.ok else 1
    if args.action == "prune":
        gone = prune(dest, keep=keep)
        print(f"pruned {len(gone)}, kept the newest {keep}")
        for key in gone:
            print("  " + key)
        return 0
    result = restore(dest, Path(args.db), key=args.key)
    print(f"restored {result.key} to {result.incoming_path}: "
          f"{result.bytes} bytes, {result.players} players in dim_player")
    print("redeploy the service; boot validates this file and renames it "
          "into place before it opens a connection")
    return 0


if __name__ == "__main__":
    # Guarded, because the audit suite imports every module in the package and
    # a module-scope SystemExit would fire during that walk.
    raise SystemExit(main())
