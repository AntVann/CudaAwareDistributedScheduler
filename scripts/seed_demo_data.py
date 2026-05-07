#!/usr/bin/env python3
"""
Seed realistic demo data spanning the past 7 days.

Wipes the jobs table and inserts ~300 historical jobs (DONE/FAILED/CANCELLED)
with realistic durations, priorities, GPU counts, and per-workload reason
strings. Then submits a handful of fresh QUEUED jobs via the API so agents
can process them live.

Usage:
    python3 scripts/seed_demo_data.py

Run AFTER `make up` and wait ~10s for agents to register.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import time
import urllib.request
from typing import Any

BASE = "http://localhost:8000"
OP_TOKEN = "local-operator-token"
COMPOSE_FILE = "deploy/docker-compose.yml"

NODES = ["node-a", "node-b"]

# (family, cmd_template, gpus, base_runtime_s, runtime_jitter_s, base_priority)
# base_runtime_s + jitter gives the wall-clock of "running" → "done"
WORKLOADS: list[tuple[str, list[str], int, float, float, int]] = [
    # ML training — long, GPU-heavy
    ("ml-train-resnet50",      ["python3", "train.py", "--model", "resnet50",  "--dataset", "imagenet"],   2, 1800,  900,  6),
    ("ml-train-resnet101",     ["python3", "train.py", "--model", "resnet101", "--dataset", "imagenet"],   2, 2400, 1200,  6),
    ("ml-train-bert-base",     ["python3", "train.py", "--model", "bert-base", "--dataset", "squad"],      4, 3600, 1800,  7),
    ("ml-train-bert-large",    ["python3", "train.py", "--model", "bert-large","--dataset", "squad"],      4, 5400, 1800,  8),
    ("ml-train-gpt2-small",    ["python3", "train.py", "--model", "gpt2-small","--dataset", "wikitext"],   1, 2700, 1200,  6),
    ("ml-train-llama-7b",      ["python3", "train.py", "--model", "llama-7b",  "--dataset", "redpajama"],  4, 7200, 3600,  9),
    ("ml-train-stable-diff",   ["python3", "train.py", "--model", "sd-v2",     "--dataset", "laion-art"],  4, 5400, 1800,  9),
    ("ml-finetune-clip",       ["python3", "finetune.py", "--model", "clip-vit-l"],                        2, 1500,  600,  5),
    ("ml-finetune-whisper",    ["python3", "finetune.py", "--model", "whisper-large"],                     2, 1800,  900,  5),
    # ML inference / eval — short
    ("ml-eval-vgg16",          ["python3", "eval.py", "--model", "vgg16"],                                 1,  150,   60,  2),
    ("ml-eval-yolov5",         ["python3", "eval.py", "--model", "yolov5"],                                2,  240,  120,  3),
    ("ml-eval-detectron2",     ["python3", "eval.py", "--model", "detectron2-fpn"],                        2,  300,  120,  3),
    ("ml-eval-mistral-7b",     ["python3", "eval.py", "--model", "mistral-7b", "--bench", "mmlu"],         2,  600,  240,  4),
    # Hyperparameter sweeps — medium
    ("ml-hyperopt-lr",         ["python3", "sweep.py", "--param", "learning_rate"],                        1,  900,  300,  4),
    ("ml-hyperopt-batch",      ["python3", "sweep.py", "--param", "batch_size"],                           1,  900,  300,  4),
    ("ml-hyperopt-dropout",    ["python3", "sweep.py", "--param", "dropout"],                              1, 1200,  300,  4),
    # Distillation
    ("ml-distill-teacher",     ["python3", "distill.py", "--role", "teacher"],                             2, 2400,  600,  5),
    ("ml-distill-student",     ["python3", "distill.py", "--role", "student"],                             2, 1800,  600,  5),
    # Data pipelines — short, low-GPU
    ("data-preprocess-cifar",  ["python3", "preprocess.py", "--dataset", "cifar10"],                       1,   90,   30,  1),
    ("data-preprocess-coco",   ["python3", "preprocess.py", "--dataset", "coco2017"],                      1,  240,   90,  1),
    ("data-augment-imagenet",  ["python3", "augment.py",   "--dataset", "imagenet-subset"],                1,  180,   60,  1),
    ("data-tokenize-c4",       ["python3", "tokenize.py", "--dataset", "c4-en"],                           1,  600,  180,  2),
    ("data-shard-laion",       ["python3", "shard.py",    "--dataset", "laion-2b"],                        1,  900,  240,  2),
    # Simulation — long, varied
    ("sim-molecular-dynamics", ["lmp_gpu", "-in", "in.lj", "-sf", "gpu"],                                  4, 3000, 1200,  6),
    ("sim-cfd-openfoam",       ["mpirun", "-np", "4", "simpleFoam", "-parallel"],                          4, 4200, 1200,  6),
    ("sim-weather-wrf",        ["wrf.exe"],                                                                2, 2400,  900,  4),
    ("sim-nbody-1m",           ["./nbody", "--n", "1000000", "--steps", "10000"],                          2, 1800,  600,  3),
    ("sim-monte-carlo-pi",     ["./mc_pi", "--samples", "10000000000"],                                    1, 1200,  300,  2),
    ("sim-protein-fold",       ["python3", "alphafold.py", "--target", "T1024"],                           2, 3600,  900,  7),
    ("sim-quantum-chem",       ["psi4", "-i", "h2o.in"],                                                   2, 2400,  900,  5),
    # Rendering — medium, IO-heavy
    ("render-blender-bmw",     ["blender", "-b", "bmw27.blend", "-E", "CYCLES", "-f", "1"],                2,  900,  300,  3),
    ("render-blender-classroom",["blender","-b","classroom.blend","-E","CYCLES","-f","1"],                 2, 1200,  300,  3),
    ("render-4k-animation",    ["blender", "-b", "anim.blend", "-E", "CYCLES", "-a"],                      2, 3600, 1200,  3),
    ("render-volumetric",      ["redshift", "-render", "fog.rs"],                                          2, 1800,  600,  3),
    # Crypto / bench — short
    ("crypto-sha256-bench",    ["./hashbench", "--algo", "sha256"],                                        1,  120,   30,  1),
    ("crypto-aes-bench",       ["./hashbench", "--algo", "aes-256-gcm"],                                   1,  120,   30,  1),
    # Genomics — medium
    ("genomics-bwa-align",     ["bwa", "mem", "-t", "8", "ref.fa", "reads.fq"],                            1,  900,  300,  4),
    ("genomics-variant-call",  ["gatk", "HaplotypeCaller", "-R", "ref.fa", "-I", "in.bam"],                1, 1500,  600,  4),
    ("genomics-rna-seq",       ["star",  "--genomeDir", "ref/", "--readFilesIn", "r1.fq", "r2.fq"],        1, 1800,  600,  4),
    ("genomics-bcftools",      ["bcftools", "call", "-c", "-v", "in.vcf"],                                 1,  600,  240,  3),
    # Astro
    ("astro-galaxy-classify",  ["python3", "classify_galaxies.py"],                                        1,  900,  300,  3),
    ("astro-lensing-sim",      ["python3", "lensing_sim.py", "--cluster", "abell-2218"],                   2, 2400,  900,  5),
    ("astro-cmb-analysis",     ["python3", "cmb_powerspec.py"],                                            1, 1200,  300,  3),
    # NLP
    ("nlp-sentiment-train",    ["python3", "train.py", "--task", "sst2"],                                  1,  600,  240,  2),
    ("nlp-ner-train",          ["python3", "train.py", "--task", "conll03"],                               1,  900,  300,  2),
    ("nlp-translation-en-de",  ["python3", "train.py", "--task", "wmt-en-de"],                             2, 3600, 1200,  6),
    ("nlp-summarize-cnn",      ["python3", "train.py", "--task", "cnn-dm"],                                2, 1800,  600,  4),
]

# Failure modes (reason, exit_code) — choose one when generating a FAILED job.
FAILURE_MODES: list[tuple[str, int]] = [
    ("CUDA OOM: tried to allocate 18.5 GiB on device 0 (cap 24 GiB)",            137),
    ("OOM killed by cgroup (exceeded 32GB GPU memory)",                          137),
    ("SIGSEGV: invalid memory access in CUDA kernel",                            139),
    ("NCCL allreduce timeout after 600s — peer node-b unreachable",              1),
    ("Timeout: exceeded 3600s wall-clock limit",                                 124),
    ("Training diverged: NaN loss detected at epoch 12",                         1),
    ("CUDA driver mismatch: container has 12.4, host driver supports 12.2",      1),
    ("GPU ECC uncorrectable error on device 0",                                  1),
    ("Disk full on /scratch — cannot write checkpoint",                          28),
    ("ImportError: libcuda.so.1 not found",                                      127),
    ("Process killed: agent watchdog detected zero-throughput for 300s",         137),
    ("MPI error: rank 2 exited prematurely",                                     1),
]


def api_post(path: str, data: dict, token: str = OP_TOKEN) -> None:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{BASE}/api/{path}",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req)
    except Exception:
        pass


def api_put(path: str, data: dict, token: str = OP_TOKEN) -> None:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{BASE}/api/{path}",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="PUT",
    )
    try:
        urllib.request.urlopen(req)
    except Exception:
        pass


def api_get(path: str) -> Any:
    req = urllib.request.Request(f"{BASE}/api/{path}")
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except Exception:
        return None


def check_health() -> bool:
    try:
        req = urllib.request.Request(f"{BASE}/health")
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read())
        return data.get("ok", False)
    except Exception:
        return False


def psql_exec(sql: str) -> bool:
    result = subprocess.run(
        [
            "docker", "compose", "-f", COMPOSE_FILE,
            "exec", "-T", "postgres",
            "psql", "-U", "overlay", "-d", "overlay", "-q",
        ],
        input=sql,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  SQL ERROR: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def sql_str(s: str) -> str:
    return s.replace("'", "''")


def make_spec(job_id: str, cmd: list[str], gpus: int, priority: int) -> str:
    return json.dumps({
        "job_id": job_id,
        "image": "",
        "cmd": cmd,
        "gpus": gpus,
        "priority": priority,
        "env": {},
        "metadata": {},
    })


def gpu_arr(n: int) -> str:
    ids = list(range(min(n, 4)))
    return "{" + ",".join(str(g) for g in ids) + "}"


def jitter(base: float, span: float, rng: random.Random) -> float:
    """Return base ± span/2, clamped to non-negative."""
    return max(0.1, base + rng.uniform(-span / 2, span / 2))


def realistic_placement_latency(rng: random.Random) -> float:
    """Most placements are sub-second; long tail to a few seconds."""
    # 80% < 500ms, 15% 500ms-2s, 5% 2s-10s
    r = rng.random()
    if r < 0.80:
        return rng.uniform(0.05, 0.5)
    if r < 0.95:
        return rng.uniform(0.5, 2.0)
    return rng.uniform(2.0, 10.0)


def build_historical_jobs(rng: random.Random, count: int, now: float) -> list[str]:
    """Generate N historical jobs across the past 7 days. Returns SQL statements."""
    statements: list[str] = []
    week_seconds = 7 * 24 * 3600
    used_ids: set[str] = set()

    # ~85% DONE, ~10% FAILED, ~5% CANCELLED
    state_choices = ["DONE"] * 85 + ["FAILED"] * 10 + ["CANCELLED"] * 5

    for i in range(count):
        family, cmd, gpus, base_rt, jitter_rt, base_prio = rng.choice(WORKLOADS)
        # Bias activity toward the last 24 hours: half of jobs in last day, rest spread over 6 days prior
        if rng.random() < 0.45:
            enqueue_offset = rng.uniform(0, 24 * 3600)
        else:
            enqueue_offset = rng.uniform(24 * 3600, week_seconds)
        enqueued = now - enqueue_offset

        placement_lat = realistic_placement_latency(rng)
        placed = enqueued + placement_lat

        run_lat = rng.uniform(0.5, 4.0)  # placed → running is fast
        running = placed + run_lat

        runtime = jitter(base_rt, jitter_rt, rng)
        # Don't let a job's terminal time be in the future
        terminal = min(running + runtime, now - 5)
        if terminal <= running:
            continue

        node = rng.choice(NODES)
        priority = max(0, base_prio + rng.randint(-1, 1))

        # Generate a unique job_id per run (family + short suffix tied to seed run)
        suffix = f"{int(now)%100000:05d}-{i:04d}"
        job_id = f"{family}-{suffix}"
        if job_id in used_ids:
            continue
        used_ids.add(job_id)

        spec = sql_str(make_spec(job_id, cmd, gpus, priority))
        state = rng.choice(state_choices)

        if state == "DONE":
            ts_obj = {
                "enqueued": round(enqueued, 3),
                "placed":   round(placed, 3),
                "running":  round(running, 3),
                "done":     round(terminal, 3),
            }
            timestamps = sql_str(json.dumps(ts_obj))
            statements.append(
                f"INSERT INTO jobs (job_id, spec, status, node_id, gpu_ids, timestamps, exit_code) "
                f"VALUES ('{job_id}', '{spec}'::jsonb, 'DONE', '{node}', '{gpu_arr(gpus)}', "
                f"'{timestamps}'::jsonb, 0);"
            )
        elif state == "FAILED":
            reason, exit_code = rng.choice(FAILURE_MODES)
            # Failed jobs often die earlier than the planned runtime
            failed_at = running + rng.uniform(5, max(6.0, runtime * rng.uniform(0.05, 0.9)))
            failed_at = min(failed_at, now - 5)
            if failed_at <= running:
                continue
            ts_obj = {
                "enqueued": round(enqueued, 3),
                "placed":   round(placed, 3),
                "running":  round(running, 3),
                "failed":   round(failed_at, 3),
            }
            timestamps = sql_str(json.dumps(ts_obj))
            reason_sql = sql_str(reason)
            statements.append(
                f"INSERT INTO jobs (job_id, spec, status, node_id, gpu_ids, timestamps, exit_code, reason) "
                f"VALUES ('{job_id}', '{spec}'::jsonb, 'FAILED', '{node}', '{gpu_arr(gpus)}', "
                f"'{timestamps}'::jsonb, {exit_code}, '{reason_sql}');"
            )
        else:  # CANCELLED
            # 60% cancelled while QUEUED (no place/run), 40% mid-run
            if rng.random() < 0.6:
                cancelled_at = enqueued + rng.uniform(5, 600)
                cancelled_at = min(cancelled_at, now - 5)
                ts_obj = {
                    "enqueued":  round(enqueued, 3),
                    "cancelled": round(cancelled_at, 3),
                }
                node_field = "NULL"
                gpu_field = "'{}'"
            else:
                cancelled_at = running + rng.uniform(10, max(11.0, runtime * 0.5))
                cancelled_at = min(cancelled_at, now - 5)
                if cancelled_at <= running:
                    continue
                ts_obj = {
                    "enqueued":  round(enqueued, 3),
                    "placed":    round(placed, 3),
                    "running":   round(running, 3),
                    "cancelled": round(cancelled_at, 3),
                }
                node_field = f"'{node}'"
                gpu_field = f"'{gpu_arr(gpus)}'"
            timestamps = sql_str(json.dumps(ts_obj))
            reason_sql = sql_str("Cancelled by operator")
            statements.append(
                f"INSERT INTO jobs (job_id, spec, status, node_id, gpu_ids, timestamps, reason) "
                f"VALUES ('{job_id}', '{spec}'::jsonb, 'CANCELLED', {node_field}, {gpu_field}::integer[], "
                f"'{timestamps}'::jsonb, '{reason_sql}');"
            )

    return statements


def build_running_jobs(rng: random.Random, now: float) -> list[str]:
    """A handful of in-progress jobs that started recently."""
    statements: list[str] = []
    for i in range(rng.randint(3, 6)):
        family, cmd, gpus, base_rt, jitter_rt, base_prio = rng.choice(WORKLOADS)
        # Started 30s - 30min ago
        enqueued = now - rng.uniform(60, 1800)
        placed = enqueued + realistic_placement_latency(rng)
        running = placed + rng.uniform(0.5, 4.0)
        node = rng.choice(NODES)
        suffix = f"{int(now)%100000:05d}-r{i:02d}"
        job_id = f"{family}-{suffix}"
        spec = sql_str(make_spec(job_id, cmd, gpus, max(0, base_prio + rng.randint(-1, 1))))
        ts_obj = {
            "enqueued": round(enqueued, 3),
            "placed":   round(placed, 3),
            "running":  round(running, 3),
        }
        timestamps = sql_str(json.dumps(ts_obj))
        statements.append(
            f"INSERT INTO jobs (job_id, spec, status, node_id, gpu_ids, timestamps) "
            f"VALUES ('{job_id}', '{spec}'::jsonb, 'RUNNING', '{node}', '{gpu_arr(gpus)}', "
            f"'{timestamps}'::jsonb);"
        )
    return statements


def main() -> None:
    print("=== Seeding realistic demo data (past 7 days) ===\n")

    print("[1/5] Checking control plane health...")
    if not check_health():
        print("ERROR: Control plane not healthy. Run 'make up' first and wait ~10s.")
        sys.exit(1)
    print("  Control plane is healthy.\n")

    rng = random.Random(0xC0DA)
    now = time.time()
    historical_count = 300

    print(f"[2/5] Wiping jobs table and inserting {historical_count} historical jobs + a few RUNNING...")
    statements = ["TRUNCATE TABLE jobs;"]
    statements.extend(build_historical_jobs(rng, historical_count, now))
    statements.extend(build_running_jobs(rng, now))

    sql = "BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n"
    if not psql_exec(sql):
        print("ERROR: Failed to insert historical jobs.")
        sys.exit(1)
    print(f"  {len(statements)-1} historical/running rows inserted.\n")

    print("[3/5] Submitting 6 fresh QUEUED jobs via API...")
    queued_specs = rng.sample(WORKLOADS, 6)
    for i, (family, cmd, gpus, _, _, base_prio) in enumerate(queued_specs):
        job_id = f"{family}-q{int(now)%100000:05d}-{i:02d}"
        api_post("jobs", {
            "job_id": job_id,
            "image": "",
            "cmd": cmd,
            "gpus": gpus,
            "priority": max(1, base_prio + rng.randint(-1, 2)),
        })
        print(f"  submitted {job_id}")
    print()

    print("[4/5] Setting scheduler policy to ROUND_ROBIN...")
    api_put("policies/active", {"policy": "ROUND_ROBIN"})
    print("  Policy set to ROUND_ROBIN.\n")

    print("[5/5] Verifying...")
    summary = api_get("jobs/summary")
    if summary:
        print(f"  Job summary: {json.dumps(summary)}")
    print()

    print("=== Demo data seeded successfully! ===")
    print(f"  ~{historical_count} historical jobs spread across the past 7 days")
    print("  ~85% DONE, ~10% FAILED (with realistic reasons), ~5% CANCELLED")
    print("  3-6 RUNNING jobs in flight, 6 fresh QUEUED jobs for live processing")
    print()
    print("Open http://localhost:5173 and enter 'local-operator-token' to explore.")


if __name__ == "__main__":
    main()
