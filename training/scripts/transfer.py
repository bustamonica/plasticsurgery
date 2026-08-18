"""Dataset transfer that survives a connection reset.

Measured on 2026-08-16: the same 349MB dataset took 5.4 min, 21.7 min, and then
134.8 min before dying on "connection reset by peer". Those three numbers are
explained entirely by per-file cost - 57, 228 and 1417 ms per file across 5707
files - not by bandwidth. `scp -r` sets up a transfer per file, so the dataset's
file COUNT is the constraint, and no amount of bandwidth or retrying fixes that.

So: one tar archive instead of 5707 files, sent in verified chunks so a reset
resumes instead of restarting. Chunks already present with a matching MD5 are
skipped, which makes a retry cheap and an interrupted run recoverable.

The transport is injected so the reset-survival path is testable without a pod
(see test_transfer.py, which fails chunks deliberately).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path

CHUNK_BYTES = 32 * 1024 * 1024
MAX_ATTEMPTS = 4


def md5_of(path: Path, chunk=1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def split_file(path: Path, out_dir: Path, chunk_bytes=CHUNK_BYTES) -> "list[Path]":
    out_dir.mkdir(parents=True, exist_ok=True)
    parts, i = [], 0
    with open(path, "rb") as f:
        while True:
            data = f.read(chunk_bytes)
            if not data:
                break
            part = out_dir / f"part.{i:04d}"
            if not (part.exists() and part.stat().st_size == len(data)):
                part.write_bytes(data)
            parts.append(part)
            i += 1
    return parts


class LocalTransport:
    """Test double. `fail_plan` maps chunk name -> number of times to fail it.

    `down_fail_plan` does the same for the DOWNLOAD direction, which is the one
    that lost a whole run on 2026-08-17 because it was never given this
    treatment.
    """

    def __init__(self, root: Path, fail_plan=None, down_fail_plan=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.fail_plan = dict(fail_plan or {})
        self.down_fail_plan = dict(down_fail_plan or {})
        self.attempts = []
        self.fetches = []

    # ---- download side ---------------------------------------------------
    def remote_split(self, remote: str, chunk_bytes: int):
        src = self.root / Path(remote).name
        d = self.root / f".split-{Path(remote).name}"
        d.mkdir(parents=True, exist_ok=True)
        out, i = [], 0
        with open(src, "rb") as f:
            while True:
                data = f.read(chunk_bytes)
                if not data:
                    break
                name = f"p{i:04d}"
                (d / name).write_bytes(data)
                out.append((name, hashlib.md5(data).hexdigest()))
                i += 1
        return str(d), out

    def fetch(self, remote_path: str, local: Path) -> bool:
        name = Path(remote_path).name
        self.fetches.append(name)
        data = Path(remote_path).read_bytes()
        if self.down_fail_plan.get(name, 0) > 0:
            self.down_fail_plan[name] -= 1
            # a reset leaves a truncated local file behind
            Path(local).write_bytes(data[: len(data) // 3])
            return False
        Path(local).write_bytes(data)
        return True

    def cleanup_split(self, split_dir: str):
        import shutil
        shutil.rmtree(split_dir, ignore_errors=True)

    def send(self, local: Path, remote: str) -> bool:
        name = Path(remote).name
        self.attempts.append(name)
        if self.fail_plan.get(name, 0) > 0:
            self.fail_plan[name] -= 1
            # a reset can leave a truncated file behind; reproduce that
            dest = self.root / name
            data = Path(local).read_bytes()
            dest.write_bytes(data[: len(data) // 3])
            return False
        (self.root / name).write_bytes(Path(local).read_bytes())
        return True

    def remote_md5(self, remote: str):
        p = self.root / Path(remote).name
        return md5_of(p) if p.exists() else None

    def assemble(self, parts: "list[str]", remote: str) -> bool:
        with open(self.root / Path(remote).name, "wb") as out:
            for p in parts:
                out.write((self.root / Path(p).name).read_bytes())
        return True


class SshTransport:
    def __init__(self, key, host, port, timeout=900):
        self.key, self.host, self.port, self.timeout = key, host, port, timeout

    def _ssh(self, cmd, timeout=None):
        return subprocess.run(
            ["ssh", "-i", self.key, "-p", str(self.port), "-o", "IdentitiesOnly=yes",
             "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
             "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=20",
             "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
             f"root@{self.host}", cmd],
            capture_output=True, text=True, timeout=timeout or self.timeout)

    def send(self, local: Path, remote: str) -> bool:
        r = subprocess.run(
            ["scp", "-i", self.key, "-P", str(self.port), "-o", "IdentitiesOnly=yes",
             "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
             "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=20",
             "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
             str(local), f"root@{self.host}:{remote}"],
            capture_output=True, text=True, timeout=self.timeout)
        return r.returncode == 0

    def remote_md5(self, remote: str):
        r = self._ssh(f"md5sum {remote} 2>/dev/null | cut -d' ' -f1", timeout=300)
        out = r.stdout.strip()
        return out or None

    def assemble(self, parts, remote: str) -> bool:
        joined = " ".join(sorted(parts))
        r = self._ssh(f"cat {joined} > {remote} && rm -f {joined}", timeout=1800)
        return r.returncode == 0

    # ---- download side ---------------------------------------------------
    def remote_split(self, remote: str, chunk_bytes: int):
        token = Path(remote).name.replace(".", "_")
        d = f"/tmp/split-{token}"
        r = self._ssh(f"rm -rf {d} && mkdir -p {d} && split -b {chunk_bytes} -d -a 4 "
                      f"{remote} {d}/p && cd {d} && md5sum p* | awk '{{print $2, $1}}'",
                      timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"remote split failed: {r.stderr[:200]}")
        rows = [ln.split() for ln in r.stdout.strip().splitlines() if ln.strip()]
        return d, [(a, b) for a, b in rows]

    def fetch(self, remote_path: str, local) -> bool:
        r = subprocess.run(
            ["scp", "-i", self.key, "-P", str(self.port), "-o", "IdentitiesOnly=yes",
             "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
             "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=20",
             "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
             f"root@{self.host}:{remote_path}", str(local)],
            capture_output=True, text=True, timeout=self.timeout)
        return r.returncode == 0

    def cleanup_split(self, split_dir: str):
        self._ssh(f"rm -rf {split_dir}", timeout=300)


def send_resumable(transport, local: Path, remote: str, work_dir: Path,
                   log=print, chunk_bytes=CHUNK_BYTES, max_attempts=MAX_ATTEMPTS) -> dict:
    """Send `local` to `remote` in verified chunks, skipping what is already there.

    Returns a report. Raises RuntimeError if a chunk cannot be landed within
    max_attempts, so the caller aborts loudly rather than training on a
    truncated dataset.
    """
    local = Path(local)
    parts = split_file(local, work_dir / "chunks", chunk_bytes)
    remote_dir = os.path.dirname(remote) or "."
    log(f"{local.name}: {local.stat().st_size / 1e6:.0f} MB in {len(parts)} chunks")

    sent, skipped, retried = 0, 0, 0
    for part in parts:
        want = md5_of(part)
        rpath = f"{remote_dir}/{part.name}"
        if transport.remote_md5(rpath) == want:
            skipped += 1
            continue
        for attempt in range(1, max_attempts + 1):
            ok = transport.send(part, rpath) and transport.remote_md5(rpath) == want
            if ok:
                sent += 1
                break
            retried += 1
            backoff = min(30, 2 ** attempt)
            log(f"  {part.name}: attempt {attempt} failed, retrying in {backoff}s")
            time.sleep(backoff if max_attempts > 2 else 0)
        else:
            raise RuntimeError(f"chunk {part.name} failed {max_attempts} attempts - aborting "
                               "rather than assembling a truncated dataset")

    if not transport.assemble([f"{remote_dir}/{p.name}" for p in parts], remote):
        raise RuntimeError("remote assemble failed")
    whole = md5_of(local)
    got = transport.remote_md5(remote)
    if got != whole:
        raise RuntimeError(f"assembled file md5 {got} != local {whole}")
    log(f"transferred and verified: {sent} chunks sent, {skipped} already present, "
        f"{retried} retries, md5 {whole[:12]} matches")
    return {"chunks": len(parts), "sent": sent, "skipped": skipped,
            "retried": retried, "md5": whole}


def safetensors_header_ok(path: Path) -> str:
    """Open it the way a loader would. A file that ARRIVED is not a file that LOADS.

    Reads the safetensors header and checks the declared tensor payload exactly
    accounts for the bytes present, which a truncated or corrupted download
    cannot satisfy.
    """
    import json
    import struct

    size = path.stat().st_size
    with open(path, "rb") as f:
        raw = f.read(8)
        if len(raw) < 8:
            return "too short to hold a header"
        n = struct.unpack("<Q", raw)[0]
        if n <= 0 or n + 8 > size:
            return f"header length {n} inconsistent with file size {size}"
        try:
            head = json.loads(f.read(n))
        except Exception as e:                                     # noqa: BLE001
            return f"header is not valid JSON: {e}"
    tensors = {k: v for k, v in head.items() if k != "__metadata__"}
    if not tensors:
        return "header declares no tensors"
    end = max(v["data_offsets"][1] for v in tensors.values())
    if 8 + n + end != size:
        return f"declared payload ends at {8 + n + end} but file is {size}"
    return f"OK: {len(tensors)} tensors, {end / 1e6:.0f}MB payload"


def recv_resumable(transport, remote: str, local: Path, work_dir: Path,
                   log=print, chunk_bytes=CHUNK_BYTES, max_attempts=MAX_ATTEMPTS,
                   validate=None) -> dict:
    """Pull `remote` to `local` in verified chunks, resuming what is already here.

    The mirror of send_resumable, and the half whose absence lost an entire
    training run: harvest used plain `scp -r` on a 6GB directory, died on a
    connection reset, and the checkpoints never left the pod.

    `validate` is an extra gate applied to the assembled file - for checkpoints
    it is safetensors_header_ok, because arriving intact and loading are
    different claims.
    """
    local = Path(local)
    local.parent.mkdir(parents=True, exist_ok=True)
    want_whole = transport.remote_md5(remote)
    split_dir, chunks = transport.remote_split(remote, chunk_bytes)
    parts_dir = Path(work_dir) / f".{local.name}.parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    log(f"{local.name}: {len(chunks)} chunks inbound")

    got, skipped, retried = 0, 0, 0
    try:
        for name, want in chunks:
            lp = parts_dir / name
            if lp.exists() and md5_of(lp) == want:
                skipped += 1
                continue
            for attempt in range(1, max_attempts + 1):
                if transport.fetch(f"{split_dir}/{name}", lp) and md5_of(lp) == want:
                    got += 1
                    break
                retried += 1
                log(f"  {name}: attempt {attempt} failed, retrying")
                time.sleep(min(20, 2 ** attempt) if max_attempts > 2 else 0)
            else:
                raise RuntimeError(f"chunk {name} failed {max_attempts} attempts - "
                                   "aborting rather than keeping a truncated checkpoint")
        with open(local, "wb") as out:
            for name, _ in chunks:
                out.write((parts_dir / name).read_bytes())
    finally:
        transport.cleanup_split(split_dir)

    whole = md5_of(local)
    if want_whole and whole != want_whole:
        local.unlink(missing_ok=True)
        raise RuntimeError(f"assembled md5 {whole} != remote {want_whole}")
    if validate:
        verdict = validate(local)
        if not verdict.startswith("OK"):
            local.unlink(missing_ok=True)
            raise RuntimeError(f"{local.name} arrived but does not load - {verdict}")
        log(f"  loads: {verdict}")
    for name, _ in chunks:
        (parts_dir / name).unlink(missing_ok=True)
    parts_dir.rmdir()
    log(f"{local.name}: VERIFIED locally - {got} chunks fetched, {skipped} already here, "
        f"{retried} retries, md5 {whole[:12]}")
    return {"chunks": len(chunks), "fetched": got, "skipped": skipped,
            "retried": retried, "md5": whole}


def parse_checkpoint_paths(text):
    """Absolute checkpoint paths only, from a blob that also holds log prose.

    ai-toolkit logs `Saved checkpoint to output/.../run_000000400.safetensors`,
    and a naive "line ends with .safetensors" test swallows that whole sentence
    as a path. Passed to `split` it becomes five operands and fails with
    "split: extra operand 'to'" - which an abort-on-failed-harvest rule reads as
    an unretrievable checkpoint, killing a healthy run over a parsing bug. So:
    absolute path, single token, real suffix.
    """
    out = []
    for line in text.splitlines():
        t = line.strip()
        if t.startswith("/") and t.endswith(".safetensors") and " " not in t:
            out.append(t)
    return out
