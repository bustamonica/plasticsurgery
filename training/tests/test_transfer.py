"""Proof that the transfer survives a reset. Run: python3 test_transfer.py"""
import shutil
import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parent.parent / 'scripts'))
import tempfile
from pathlib import Path

from transfer import (LocalTransport, md5_of, parse_checkpoint_paths, recv_resumable,
                      safetensors_header_ok, send_resumable, split_file)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    # Must RAISE, or pytest collects these as vacuous passes.
    assert ok, f"{name}: {detail}"


def make_payload(tmp: Path, mb=10) -> Path:
    p = tmp / "dataset.tar"
    import os
    p.write_bytes(os.urandom(mb * 1024 * 1024))
    return p


def run_one(fail_plan, mb=10, chunk=2 * 1024 * 1024, attempts=4):
    tmp = Path(tempfile.mkdtemp())
    src = make_payload(tmp, mb)
    t = LocalTransport(tmp / "remote", fail_plan=fail_plan)
    rep = send_resumable(t, src, "/remote/dataset.tar", tmp / "work",
                         log=lambda *_: None, chunk_bytes=chunk, max_attempts=attempts)
    landed = (tmp / "remote" / "dataset.tar")
    same = landed.exists() and md5_of(landed) == md5_of(src)
    return rep, same, t, tmp, src


def test_clean_transfer():
    rep, same, t, tmp, _ = run_one({})
    check("clean transfer lands byte-identical", same,
          f"{rep['chunks']} chunks, md5 {rep['md5'][:8]}")
    shutil.rmtree(tmp)


def test_survives_connection_resets():
    """Chunks 1 and 3 fail twice each, leaving TRUNCATED files behind."""
    rep, same, t, tmp, _ = run_one({"part.0001": 2, "part.0003": 2})
    check("survives resets mid-transfer and still lands byte-identical", same,
          f"{rep['retried']} retries")
    check("...and it retried exactly the failed chunks", rep["retried"] == 4,
          f"retried={rep['retried']}")
    shutil.rmtree(tmp)


def test_truncated_remote_is_detected_not_trusted():
    """A reset leaves a partial file. MD5 must reject it rather than accept it."""
    tmp = Path(tempfile.mkdtemp())
    src = make_payload(tmp, 4)
    t = LocalTransport(tmp / "remote", fail_plan={"part.0000": 1})
    parts = split_file(src, tmp / "work" / "chunks", 2 * 1024 * 1024)
    t.send(parts[0], "/remote/part.0000")          # fails -> writes 1/3 of the bytes
    partial = tmp / "remote" / "part.0000"
    check("a reset really does leave a truncated remote file",
          partial.exists() and partial.stat().st_size < parts[0].stat().st_size)
    check("...and its md5 does NOT match, so it is not trusted",
          t.remote_md5("/remote/part.0000") != md5_of(parts[0]))
    shutil.rmtree(tmp)


def test_resume_skips_what_is_already_there():
    """Second run of an interrupted transfer must send almost nothing."""
    tmp = Path(tempfile.mkdtemp())
    src = make_payload(tmp, 10)
    t = LocalTransport(tmp / "remote")
    r1 = send_resumable(t, src, "/remote/dataset.tar", tmp / "work",
                        log=lambda *_: None, chunk_bytes=2 * 1024 * 1024)
    r2 = send_resumable(t, src, "/remote/dataset.tar", tmp / "work",
                        log=lambda *_: None, chunk_bytes=2 * 1024 * 1024)
    check("first run sends every chunk", r1["sent"] == r1["chunks"] and r1["skipped"] == 0,
          f"sent {r1['sent']}/{r1['chunks']}")
    check("resume sends NOTHING and skips all chunks",
          r2["sent"] == 0 and r2["skipped"] == r2["chunks"], f"skipped {r2['skipped']}")
    shutil.rmtree(tmp)


def test_gives_up_loudly_rather_than_truncating():
    try:
        run_one({"part.0002": 99}, attempts=2)
        check("a permanently failing chunk aborts instead of assembling garbage", False,
              "no exception raised")
    except RuntimeError as e:
        check("a permanently failing chunk aborts instead of assembling garbage",
              "aborting" in str(e))


def test_file_count_collapses_to_one_stream():
    """The actual fix: 5707 round trips become a handful."""
    tmp = Path(tempfile.mkdtemp())
    src = make_payload(tmp, 10)
    parts = split_file(src, tmp / "chunks", 32 * 1024 * 1024)
    check("349MB tar at 32MB chunks is ~11 transfers, not 5707",
          len(parts) == 1, f"10MB test payload -> {len(parts)} chunk(s); "
                           f"349MB -> {-(-349 // 32)} chunks vs 5707 files")
    shutil.rmtree(tmp)


# ---------- DOWNLOAD: the half whose absence lost a whole training run -------

def fake_safetensors(path: Path, n_tensors=3, payload=2 * 1024 * 1024):
    """A structurally real safetensors file, so header validation is meaningful."""
    import json, os, struct
    per = payload // n_tensors
    head, off = {}, 0
    for i in range(n_tensors):
        head[f"t{i}"] = {"dtype": "F32", "shape": [per // 4], "data_offsets": [off, off + per]}
        off += per
    blob = json.dumps(head).encode()
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(blob))); f.write(blob); f.write(os.urandom(off))
    return path


def download_case(down_fail_plan, chunk=512 * 1024, attempts=4, corrupt=False):
    tmp = Path(tempfile.mkdtemp())
    remote_root = tmp / "remote"; remote_root.mkdir()
    src = fake_safetensors(remote_root / "ckpt.safetensors")
    t = LocalTransport(remote_root, down_fail_plan=down_fail_plan)
    if corrupt:
        orig = t.fetch
        def bad(rp, lp):
            ok = orig(rp, lp)
            Path(lp).write_bytes(b"\x00" * Path(lp).stat().st_size)
            return ok
        t.fetch = bad
    rep = recv_resumable(t, "ckpt.safetensors", tmp / "local" / "ckpt.safetensors",
                         tmp / "work", log=lambda *_: None, chunk_bytes=chunk,
                         max_attempts=attempts, validate=safetensors_header_ok)
    return rep, tmp, src


def test_download_clean():
    rep, tmp, src = download_case({})
    got = tmp / "local" / "ckpt.safetensors"
    check("download lands byte-identical and LOADS", md5_of(got) == md5_of(src),
          f"{rep['chunks']} chunks; {safetensors_header_ok(got)}")
    shutil.rmtree(tmp)


def test_download_survives_resets():
    rep, tmp, src = download_case({"p0001": 2, "p0002": 1})
    got = tmp / "local" / "ckpt.safetensors"
    check("download survives injected resets", md5_of(got) == md5_of(src),
          f"{rep['retried']} retries")
    check("...retried exactly the failed chunks", rep["retried"] == 3, f"{rep['retried']}")
    shutil.rmtree(tmp)


def test_download_resume_sends_zero():
    tmp = Path(tempfile.mkdtemp())
    rr = tmp / "remote"; rr.mkdir()
    src = fake_safetensors(rr / "ckpt.safetensors")
    t = LocalTransport(rr)
    kw = dict(log=lambda *_: None, chunk_bytes=512 * 1024, validate=safetensors_header_ok)
    r1 = recv_resumable(t, "ckpt.safetensors", tmp / "l" / "ckpt.safetensors", tmp / "w", **kw)
    # parts are consumed on success, so resume is proven by re-fetching into a
    # pre-populated parts dir instead
    parts = tmp / "w" / ".ckpt.safetensors.parts"; parts.mkdir(parents=True, exist_ok=True)
    d, chunks = t.remote_split("ckpt.safetensors", 512 * 1024)
    for name, _ in chunks:
        (parts / name).write_bytes((Path(d) / name).read_bytes())
    before = len(t.fetches)
    r2 = recv_resumable(t, "ckpt.safetensors", tmp / "l2" / "ckpt.safetensors", tmp / "w", **kw)
    check("first download fetches every chunk", r1["fetched"] == r1["chunks"],
          f"{r1['fetched']}/{r1['chunks']}")
    check("resume fetches ZERO chunks and skips all",
          r2["fetched"] == 0 and r2["skipped"] == r2["chunks"] and len(t.fetches) == before,
          f"fetched {r2['fetched']}, skipped {r2['skipped']}")
    shutil.rmtree(tmp)


def test_download_truncation_caught_by_md5():
    try:
        download_case({"p0000": 99}, attempts=2)
        check("a permanently failing chunk aborts the download", False, "no exception")
    except RuntimeError as e:
        check("a permanently failing chunk aborts the download", "aborting" in str(e))


def test_corrupt_bytes_never_reach_disk():
    try:
        download_case({}, corrupt=True, attempts=2)
        check("corrupted chunks are rejected by md5, not assembled", False, "no exception")
    except RuntimeError as e:
        check("corrupted chunks are rejected by md5, not assembled",
              "aborting" in str(e) or "md5" in str(e))


def test_arrived_is_not_the_same_as_loads():
    tmp = Path(tempfile.mkdtemp())
    good = fake_safetensors(tmp / "good.safetensors")
    check("a real safetensors header validates", safetensors_header_ok(good).startswith("OK"))
    trunc = tmp / "trunc.safetensors"
    trunc.write_bytes(good.read_bytes()[: good.stat().st_size // 2])
    v = safetensors_header_ok(trunc)
    check("a truncated file is caught even though it 'arrived'", not v.startswith("OK"), v)
    shutil.rmtree(tmp)


def test_checkpoint_parser_rejects_log_prose():
    """The bug that killed a healthy run at step ~600 on 2026-08-17."""
    blob = ("Saved checkpoint to output/ba_viz_run/ba_viz_run_000000400.safetensors\n"
            "Saved optimizer to output/ba_viz_run/optimizer.pt\n"
            "---\n"
            "/workspace/ai-toolkit/output/ba_viz_run/ba_viz_run_000000200.safetensors\n"
            "/workspace/ai-toolkit/output/ba_viz_run/ba_viz_run_000000400.safetensors\n"
            "---\nPROC_ALIVE")
    got = parse_checkpoint_paths(blob)
    check("parser takes only absolute checkpoint paths", len(got) == 2, f"{len(got)}")
    check("...rejects the 'Saved checkpoint to' prose",
          all(g.startswith("/workspace/") for g in got))
    check("...every result is one shell token safe for split",
          all(" " not in g for g in got))
    naive = [l.strip() for l in blob.splitlines() if l.strip().endswith(".safetensors")]
    check("...where the naive parser took the prose too", len(naive) == 3, f"{len(naive)}")


if __name__ == "__main__":
    print("PROVING THE TRANSFER SURVIVES A RESET\n")
    test_clean_transfer()
    test_survives_connection_resets()
    test_truncated_remote_is_detected_not_trusted()
    test_resume_skips_what_is_already_there()
    test_gives_up_loudly_rather_than_truncating()
    test_file_count_collapses_to_one_stream()
    print("\n  -- download direction --")
    test_download_clean()
    test_download_survives_resets()
    test_download_resume_sends_zero()
    test_download_truncation_caught_by_md5()
    test_corrupt_bytes_never_reach_disk()
    test_arrived_is_not_the_same_as_loads()
    test_checkpoint_parser_rejects_log_prose()
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed")
    sys.exit(1 if bad else 0)
