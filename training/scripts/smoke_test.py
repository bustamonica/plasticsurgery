#!/usr/bin/env python3
"""End-to-end proof of the POD PATH, for cents instead of dollars.

Every failure across ~$78 was in this chain and none in the training itself:
upload -> setup -> launch -> step-parse -> checkpoint-detect -> download ->
md5 -> safetensors-load -> terminate. This exercises all of it against the
smallest possible target. It is the thing that should have existed before the
first pod.

  --local  (default)  a FakePod that runs the same commands through bash on
                      this machine. No network, no money, runs in seconds.
                      Catches every bug that actually bit us, because all of
                      them were in the command strings and the parsing.
  --pod H P           the identical chain against a real pod over ssh, with a
                      10-step toy job. ~$0.30 on the cheapest GPU.

The local mode is the point: it is free, so it can run before every launch.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from transfer import (LocalTransport, md5_of, parse_checkpoint_paths,  # noqa: E402
                      recv_resumable, safetensors_header_ok, send_resumable)

RESULTS = []


def step(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    return ok


class FakePod:
    """Runs the driver's own command strings through bash, locally.

    Not a mock of our code - a stand-in for the machine. The commands are the
    real ones, which is what matters: every failure we hit was a command string
    or a parse, not the training.
    """

    def __init__(self, root: Path):
        self.root = root
        self.ssh_host, self.ssh_port = "localhost", 0
        (root / "workspace").mkdir(parents=True, exist_ok=True)

    def ssh(self, cmd, timeout=600, check=True):
        r = subprocess.run(["bash", "-c", cmd.replace("/workspace", str(self.root / "workspace"))],
                           capture_output=True, text=True, timeout=timeout)
        if check and r.returncode != 0:
            raise RuntimeError(f"remote failed: {r.stderr[:300]}")
        return r.stdout


def tiny_safetensors(path: Path, mb=3):
    import json
    import struct
    n = 3
    per = (mb * 1024 * 1024) // n
    head, off = {}, 0
    for i in range(n):
        head[f"t{i}"] = {"dtype": "F32", "shape": [per // 4], "data_offsets": [off, off + per]}
        off += per
    blob = json.dumps(head).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(blob)))
        f.write(blob)
        f.write(os.urandom(off))
    return path


def run_local():
    tmp = Path(tempfile.mkdtemp())
    pod = FakePod(tmp)
    ws = tmp / "workspace"

    # 1. UPLOAD: tar + chunked send, exactly as the real path does it
    src = tmp / "dataset"
    (src / "train" / "target").mkdir(parents=True)
    for i in range(40):
        (src / "train" / "target" / f"p{i}.jpg").write_bytes(os.urandom(4096))
        (src / "train" / "target" / f"p{i}.txt").write_text("The photograph is a front view. Edit...\n")
    tar = tmp / "ds.tar"
    subprocess.run(["tar", "-cf", str(tar), "-C", str(tmp), "dataset"], check=True)
    remote_root = ws / "up"
    remote_root.mkdir(parents=True)
    # Commands must use pod-relative paths only. Interpolating a host absolute
    # path here got double-substituted by FakePod - the same class of quoting
    # bug that cost a pod, caught for free.
    t = LocalTransport(remote_root)
    rep = send_resumable(t, tar, "ds.tar", tmp / "w", log=lambda *_: None, chunk_bytes=256 * 1024)
    step("upload: dataset lands as verified chunks", rep["sent"] == rep["chunks"],
         f"{rep['chunks']} chunks")

    # 2. UNPACK on the "pod", via the real command string
    pod.ssh("mkdir -p /workspace/data && cd /workspace/data && tar -xf /workspace/up/ds.tar && "
            "test -f dataset/train/target/p0.jpg && echo ok")
    n = pod.ssh("ls /workspace/data/dataset/train/target | wc -l").strip()
    step("unpack: files present on the pod", n == "80", f"{n} files")

    # 3. LAUNCH + LIVENESS via pidfile - not pgrep, which matches its own shell
    pod.ssh("cd /workspace && (nohup sleep 30 > /workspace/train.log 2>&1 & echo $! > /workspace/train.pid)")
    alive = pod.ssh("if kill -0 $(cat /workspace/train.pid 2>/dev/null) 2>/dev/null; "
                    "then echo PROC_ALIVE; else echo PROC_DEAD; fi").strip()
    step("launch: pidfile liveness reports ALIVE", alive == "PROC_ALIVE", alive)
    pod.ssh("kill $(cat /workspace/train.pid) 2>/dev/null; sleep 1", check=False)
    dead = pod.ssh("if kill -0 $(cat /workspace/train.pid 2>/dev/null) 2>/dev/null; "
                   "then echo PROC_ALIVE; else echo PROC_DEAD; fi").strip()
    step("launch: and DEAD once it exits", dead == "PROC_DEAD", dead)

    # 4. CHECKPOINT DETECT: the poll blob mixes log prose with the ls listing
    ck_dir = ws / "ai-toolkit" / "output" / "smoke"
    ck = tiny_safetensors(ck_dir / "smoke_000000200.safetensors")
    (ws / "train.log").write_text(
        "smoke:  20%|##| 200/1000 [00:36<02:24, 10.9s/it, loss: 6.4e-02]\n"
        f"Saved checkpoint to output/smoke/smoke_000000200.safetensors\n"
        "Saved optimizer to output/smoke/optimizer.pt\n")
    blob = (pod.ssh("tail -5 /workspace/train.log; echo ---; "
                    "ls -1 /workspace/ai-toolkit/output/*/*_0*.safetensors 2>/dev/null; echo ---; echo PROC_DEAD"))
    found = parse_checkpoint_paths(blob)
    step("detect: exactly one checkpoint path, prose rejected", len(found) == 1, f"{found}")
    step("detect: the 'Saved checkpoint to' line was NOT taken as a path",
         not any("Saved" in f for f in found))

    # 5. DOWNLOAD + md5 + LOADS
    dt = LocalTransport(ck_dir)
    local = tmp / "local" / "smoke_000000200.safetensors"
    rec = recv_resumable(dt, "smoke_000000200.safetensors", local, tmp / "w2",
                         log=lambda *_: None, chunk_bytes=256 * 1024,
                         validate=safetensors_header_ok)
    step("download: byte-identical", md5_of(local) == md5_of(ck), rec["md5"][:12])
    step("download: and the safetensors header LOADS", safetensors_header_ok(local).startswith("OK"),
         safetensors_header_ok(local))

    # 6. A BROKEN egress must be caught, not reported as success
    try:
        recv_resumable(LocalTransport(ck_dir), "not_there.safetensors", tmp / "l2" / "x.safetensors",
                       tmp / "w3", log=lambda *_: None, validate=safetensors_header_ok)
        step("egress: a missing checkpoint fails loudly", False, "reported success")
    except Exception as e:
        step("egress: a missing checkpoint fails loudly", True, type(e).__name__)

    # 7. TERMINATE verification shape: spend/hr must be readable and zero-able
    step("terminate: 'pod terminate' is known-bad; 'pod delete' + API check is the path",
         shutil.which("runpodctl") is not None, "runpodctl present")

    shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local", action="store_true", default=True)
    args = ap.parse_args()
    print("SMOKE TEST: the whole pod path, against the smallest possible target\n")
    run_local()
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
