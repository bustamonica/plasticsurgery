# Training a LoRA by hand on RunPod

For running the training yourself, without any driver code. Every step here has
been executed and measured; the numbers are real, not estimates.

**Why by hand:** across ~$78 of attempts, every single failure was in
orchestration — pod setup, file transfer, checkpoint retrieval — and none was in
the training itself. Training runs fine. Follow this top to bottom and you get a
checkpoint on local disk.

## What it costs

| Fact | Measured |
| --- | --- |
| Step rate, H100 80GB HBM3 (SXM), bf16 | **10.9 s/optimizer-step** |
| Billed rate, secure cloud + 120GB disk | **~$3.32/h all-in** |
| Cost per 200-step checkpoint | **~$2** |
| Setup before step 1 (boot, upload, install, model pull) | ~15 min, ~$0.80 |
| Dataset upload, 366MB as chunks | **~2 min** (as 5707 separate files: 20-135 min, often failing) |

A checkpoint every 200 steps means you can stop whenever you like and still keep
what you paid for. **1000 steps ≈ 3h ≈ $10.**

## 0. Before you start

```bash
export RUNPOD_API_KEY=$(cat ~/firstmate/state/ba-viz-smoke-run.secret)
caffeinate -i &            # SEE WARNING BELOW
```

> **Stop your Mac sleeping.** This is what cost a whole balance. The Mac entered
> Idle Sleep 29 seconds after a poll, stayed asleep ~7h, and the pod billed the
> entire time because every local guard was suspended with the laptop. `caffeinate -i`
> prevents idle sleep. Do not rely on any local watchdog to save you — it sleeps too.

A dataset is already built and verified at
`~/firstmate/data/ba-viz-train-conditioning/dataset` (1902 pairs, zero heavenly,
zero EXIF, patient-level train/val split), so the plumbing below can be run
against it as-is. It is a snapshot: it predates the 2026-08-19 Etna collection,
which roughly doubled the finished corpus (current coverage per axis is in
`AGENTS.md`). Rebuild it with `scripts/build_dataset.py` from the finished corpus
before a run whose results are meant to stand.

## 1. Create the pod

```bash
runpodctl pod create \
  --name ba-viz-manual \
  --image runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404 \
  --gpu-id "NVIDIA H100 80GB HBM3" \
  --cloud-type SECURE \
  --container-disk-in-gb 120 \
  --ports 22/tcp \
  --terminate-after "$(date -u -v+4H '+%Y-%m-%dT%H:%M:%SZ')" \
  -o json
```

- **`--ports 22/tcp` is not optional.** Without it no SSH port is ever mapped and
  the pod bills unreachable. This alone once cost $20.23.
- **`--terminate-after` is your only guard that survives a sleeping laptop.** Set
  it to your run estimate plus an hour. Do not set it to 24h.
- `--cloud-type SECURE` because these are consented patient photographs. Community
  cloud is cheaper and better stocked, but that is a captain decision, not a default.
- If you get *"no longer any instances available"*, no pod was created and you were
  not charged. Try `NVIDIA A100-SXM4-80GB` — cost per step is nearly identical
  ($0.0149 vs $0.0152), it just takes twice the wall-clock.

Get the SSH endpoint:

```bash
runpodctl ssh info <POD_ID>          # or: runpodctl pod get <POD_ID>
export H=root@<IP>  P=<PORT>  K=~/.runpod/ssh/runpodctl-ssh-key
alias pssh='ssh -i $K -p $P -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null $H'
```

## 2. Upload the dataset — as ONE archive

**Never `scp -r` the dataset directory.** `scp` opens a transfer per file; with
5707 files the file *count* is the constraint, not the size. Measured across three
hosts: 57, 228 and 1417 ms **per file** — 5.4 min, 21.7 min, then a connection
reset after 134.8 minutes. As one archive it is ~2 minutes.

```bash
cd ~/firstmate/data/ba-viz-train-conditioning
tar -cf /tmp/ds.tar dataset                       # ~2s, 366MB
pssh 'mkdir -p /workspace/data /workspace/cfg'
scp -i $K -P $P /tmp/ds.tar $H:/workspace/ds.tar  # ~2 min
pssh 'cd /workspace/data && tar -xf /workspace/ds.tar && rm /workspace/ds.tar && ls dataset/train/target | wc -l'
# expect 3408  (1704 images + 1704 captions)
```

Upload the config too:

```bash
scp -i $K -P $P ~/.treehouse/*/plasticsurgery/training/configs/qwen_edit_lora.yaml \
    $H:/workspace/cfg/qwen_edit_lora.yaml
```

## 3. Install ai-toolkit — via a venv, or pip refuses

```bash
pssh 'set -e
cd /workspace
python3 -m venv --system-site-packages venv     # PEP 668: see note
venv/bin/python -m pip install -q --upgrade pip
git clone https://github.com/ostris/ai-toolkit
cd ai-toolkit && git checkout 6d8afa5684000b69db97cc40504a972a85615e3b
venv/bin/python -m pip install -q -r requirements.txt
venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.get_device_name(0))"'
```

> The image is Ubuntu 24.04, whose system Python is PEP 668 *externally managed* —
> a bare `pip install` refuses outright with `error: externally-managed-environment`.
> `--system-site-packages` satisfies PEP 668 **and** inherits the image's
> CUDA-matched torch instead of downloading a different build. Takes ~60s.

The commit is pinned because our config was validated against it; ai-toolkit renames
config keys often.

## 4. Configure the run

Copy the config in and set two things: the step count, and **turn quantization off**
(the committed config carries ai-toolkit's 32GB recipe, which an 80GB card does not
need and which costs 45% in speed — 24.0 s/step vs 10.9).

```bash
pssh 'cd /workspace && venv/bin/python - <<PY
import yaml
c = yaml.safe_load(open("cfg/qwen_edit_lora.yaml"))
p = c["config"]["process"][0]
p["model"]["quantize"] = False        # 10.9 s/step instead of 24.0
p["model"]["low_vram"] = False
p["train"]["steps"] = 1000            # <-- YOUR STEP COUNT
p["save"]["save_every"] = 200         # a checkpoint every ~36 min / ~$2
p["save"]["max_step_saves_to_keep"] = 20
c["config"]["name"] = "manual_run"
yaml.safe_dump(c, open("ai-toolkit/config/manual_run.yaml","w"), sort_keys=False)
print("wrote manual_run.yaml")
PY'
```

## 5. Launch, detached

```bash
pssh 'cd /workspace/ai-toolkit && nohup env HF_HOME=/workspace/hf \
      /workspace/venv/bin/python run.py config/manual_run.yaml \
      > /workspace/train.log 2>&1 & echo $! > /workspace/train.pid; sleep 2; cat /workspace/train.pid'
```

`nohup` so it survives your SSH session dropping. First step comes ~10 min later —
the 57GB base model downloads first. Watch it:

```bash
pssh 'tail -5 /workspace/train.log'
pssh 'kill -0 $(cat /workspace/train.pid) && echo ALIVE || echo DEAD'
```

> Check liveness with the **pidfile**, not `pgrep -f "run.py config/..."` — that
> pattern also matches the shell running the pgrep, so it reports ALIVE forever.

## 6. Checkpoints — pull each one as it appears

Checkpoints land at `/workspace/ai-toolkit/output/manual_run/manual_run_0000NNNNNN.safetensors`,
**590MB each**, one every 200 steps.

```bash
pssh 'ls -1 /workspace/ai-toolkit/output/manual_run/*_0*.safetensors'
```

Pull one down — **do this as each appears, not at the end**:

```bash
scp -i $K -P $P $H:/workspace/ai-toolkit/output/manual_run/manual_run_000000200.safetensors \
    ~/firstmate/data/ba-viz-train-conditioning/work/checkpoints/
```

> A completed 1600-step run was lost this way: harvest was left until the end,
> the transfer failed, and the pod was reclaimed with all 8 checkpoints on it.
> **A checkpoint that cannot leave the pod is not an artifact.** If a pull fails,
> stop training and fix the pull — do not keep training to a machine you cannot
> get files off.

If a single 590MB `scp` keeps resetting, send it in chunks with resume:

```bash
pssh 'cd /tmp && rm -rf ck && mkdir ck && split -b 32M -d -a 3 \
      /workspace/ai-toolkit/output/manual_run/manual_run_000000200.safetensors ck/p && md5sum ck/p*'
scp -i $K -P $P "$H:/tmp/ck/p*" ./parts/          # re-run to resume; verify md5s
cat parts/p* > manual_run_000000200.safetensors
```

## 7. Verify it actually LOADS

A file that arrived is not a file that loads. Check the md5 matches **and** that
the safetensors header parses:

```bash
pssh 'md5sum /workspace/ai-toolkit/output/manual_run/manual_run_000000200.safetensors'
md5 ~/firstmate/data/ba-viz-train-conditioning/work/checkpoints/manual_run_000000200.safetensors

python3 ~/.treehouse/*/plasticsurgery/training/scripts/transfer.py 2>/dev/null || true
python3 -c "
import sys; sys.path.insert(0, '$(echo ~/.treehouse/*/plasticsurgery/training/scripts)')
from pathlib import Path
from transfer import safetensors_header_ok
p = Path.home()/'firstmate/data/ba-viz-train-conditioning/work/checkpoints/manual_run_000000200.safetensors'
print(p.stat().st_size, 'bytes ->', safetensors_header_ok(p))"
```

Expect `OK: <N> tensors, <M>MB payload`. Anything else means a truncated or
corrupt download — do not keep it.

## 8. Terminate — and verify it is gone

```bash
runpodctl pod delete <POD_ID>
runpodctl pod list                 # must not list it
```

> **`runpodctl pod terminate` is not a real subcommand.** It exits 0 while doing
> nothing. Use `pod delete`, then confirm with `pod list` — or check the account
> directly, which is the only reliable proof:

```bash
curl -s -X POST "https://api.runpod.io/graphql?api_key=$RUNPOD_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"query":"query { myself { pods { id desiredStatus costPerHr } currentSpendPerHr clientBalance } }"}'
```

`currentSpendPerHr` must be `0`. If it is not, something is still billing.

## 9. Look at the result

```bash
cd ~/firstmate/data/ba-viz-train-conditioning
python3 make_sheets.py work/evalout captain-sheets && open captain-sheets/index.html
```

That needs eval images; to generate them, run `evaluate.py` on the pod **before**
terminating it (it needs the GPU), then pull `evalout/` down the same way as a
checkpoint. Sampling ~400 images takes ~1-2h and ~$5.

## What to expect from the model

Be sceptical of it in these specific ways:

- **Volume (cc) is the strongest axis**, but only a middle band of it is densely
  populated: on the 1902-pair build above, the only long contiguous run of
  well-populated 10cc buckets was 250-389cc. Read the current band off `AGENTS.md`
  for the dataset you actually train on, and treat a granularity claim outside it
  as unsupported by the data.
- **10cc granularity is unproven.** The caption states the exact figure, so it is
  *possible*; whether the model resolves a 10cc step above its own seed noise has
  never been measured. Generate the same prompt twice with different seeds first —
  that difference is the floor any real effect must clear.
- **extra-high profile should be expected to fail.** 6 pairs from 4 patients on
  the build above, and 12 from 6 after the 2026-08-19 collection - only 3 of them
  lateral, and lateral is the only view where profile is visible at all. It stays
  gated in the configurator for exactly this reason.
- **Right-facing views are the weakest** - about a third fewer examples than
  left-facing, on the build above and on the corpus since (per-view counts in
  `AGENTS.md`).
- **Profile only shows on side/oblique views.** At a fixed volume, moderate and high
  look nearly identical from the front; the difference is forward projection.
- Compare everything against the **base model with no LoRA loaded**. If stock
  Qwen-Image-Edit already separates 300cc from 600cc as well as the fine-tune does,
  the fine-tune added nothing on that axis.

## Before you launch anything: run the smoke test

```bash
python3 training/scripts/smoke_test.py
```

Seconds, no network, no money. It drives the whole chain — upload, unpack,
launch, pidfile liveness, checkpoint-detect, download, md5, safetensors-load,
egress-failure, terminate — against a 3MB stand-in using the same command
strings the real path uses. Every failure this project hit was in that chain,
so a green smoke test is what "the plumbing works" means. Expect 10/10.
