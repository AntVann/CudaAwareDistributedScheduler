#!/usr/bin/env python3
"""
Seed demo data for professor presentation.
Run AFTER `make up` and wait ~10s for agents to register.

Usage:
    python3 scripts/seed_demo_data.py

Inserts historical jobs (DONE/FAILED/RUNNING) directly into Postgres
via docker compose exec, then submits QUEUED jobs via the API so
agents can process them live during the demo.
"""

import json
import subprocess
import sys
import time
import urllib.request

BASE = "http://localhost:8000"
OP_TOKEN = "local-operator-token"
COMPOSE_FILE = "deploy/docker-compose.yml"


def api_post(path, data, token=OP_TOKEN):
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{BASE}/api/{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req)
    except Exception:
        pass


def api_put(path, data, token=OP_TOKEN):
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{BASE}/api/{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    try:
        urllib.request.urlopen(req)
    except Exception:
        pass


def api_get(path):
    req = urllib.request.Request(f"{BASE}/api/{path}")
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except Exception:
        return None


def check_health():
    try:
        req = urllib.request.Request(f"{BASE}/health")
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read())
        return data.get("ok", False)
    except Exception:
        return False


def psql_exec(sql):
    result = subprocess.run(
        [
            "docker", "compose", "-f", COMPOSE_FILE,
            "exec", "-T", "postgres",
            "psql", "-U", "overlay", "-d", "overlay",
        ],
        input=sql,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  SQL ERROR: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def ts(offset):
    return round(time.time() + offset, 3)


def make_spec(job_id, cmd, gpus=1, priority=0):
    return json.dumps({
        "job_id": job_id,
        "image": "",
        "cmd": cmd,
        "gpus": gpus,
        "priority": priority,
        "env": {},
        "metadata": {},
    })


def gpu_arr(n):
    ids = list(range(min(n, 4)))
    return "{" + ",".join(str(g) for g in ids) + "}"


def sql_str(s):
    return s.replace("'", "''")


def main():
    print("=== Seeding demo data ===\n")

    # --- 1. Health check ---
    print("[1/5] Checking control plane health...")
    if not check_health():
        print("ERROR: Control plane not healthy. Run 'make up' first and wait ~10s.")
        sys.exit(1)
    print("  Control plane is healthy.\n")

    # --- 2. Build SQL for historical jobs ---
    print("[2/5] Inserting historical jobs into Postgres...")

    statements = []

    # 25 DONE jobs spread over the last ~45 minutes
    done_jobs = [
        ("ml-train-resnet50",      ["python3", "-c", "print('Training ResNet-50 on ImageNet')"],   2, 5, "node-a", -2700, -2698.5, -2697, -2400),
        ("ml-train-bert-base",     ["python3", "-c", "print('Fine-tuning BERT-base on SQuAD')"],   4, 8, "node-b", -2650, -2648,   -2646, -2200),
        ("ml-train-gpt2-small",    ["python3", "-c", "print('Training GPT-2 small LM')"],          1, 7, "node-a", -2600, -2599,   -2598, -2100),
        ("ml-eval-vgg16",          ["python3", "-c", "print('Evaluating VGG-16 accuracy')"],       1, 2, "node-b", -2500, -2499.2, -2498, -2350),
        ("ml-eval-yolov5",         ["python3", "-c", "print('YOLOv5 inference benchmark')"],       2, 3, "node-a", -2400, -2398.5, -2397, -2200),
        ("data-preprocess-cifar",  ["echo", "Preprocessing CIFAR-10 dataset"],                     1, 1, "node-b", -2300, -2299.3, -2298, -2250),
        ("data-preprocess-coco",   ["echo", "Preprocessing COCO 2017 annotations"],                1, 1, "node-a", -2200, -2199.1, -2198, -2050),
        ("data-augment-imagenet",  ["echo", "Augmenting ImageNet subset"],                         1, 1, "node-b", -2100, -2099.5, -2099, -1950),
        ("sim-molecular-dynamics", ["echo", "Running LAMMPS molecular dynamics"],                   4, 6, "node-a", -2000, -1998,   -1996, -1500),
        ("sim-cfd-openfoam",       ["echo", "Running OpenFOAM CFD simulation"],                    4, 6, "node-b", -1900, -1897,   -1895, -1400),
        ("sim-weather-wrf",        ["echo", "Running WRF weather model"],                          2, 4, "node-a", -1800, -1798.5, -1797, -1500),
        ("sim-nbody-1m",           ["echo", "N-body simulation (1M particles)"],                   2, 3, "node-b", -1700, -1698,   -1696, -1400),
        ("sim-monte-carlo-pi",     ["echo", "Monte Carlo pi estimation (10B samples)"],            1, 2, "node-a", -1600, -1599,   -1598, -1500),
        ("render-blender-scene01", ["echo", "Rendering Blender scene 01"],                         2, 3, "node-b", -1400, -1398,   -1396, -1100),
        ("render-blender-scene02", ["echo", "Rendering Blender scene 02"],                         2, 3, "node-a", -1300, -1298.5, -1297, -1000),
        ("crypto-sha256-bench",    ["echo", "SHA-256 GPU benchmark"],                              1, 1, "node-b", -1200, -1199.5, -1199, -1100),
        ("ml-hyperopt-lr-sweep",   ["python3", "-c", "print('Hyperparameter sweep: LR')"],         1, 4, "node-a", -1100, -1099,   -1098, -900),
        ("ml-hyperopt-batch-sz",   ["python3", "-c", "print('Hyperparameter sweep: batch')"],      1, 4, "node-b", -1000, -999,    -998,  -800),
        ("ml-distill-teacher",     ["python3", "-c", "print('Knowledge distill: teacher')"],       2, 5, "node-a", -900,  -898,    -896,  -600),
        ("ml-distill-student",     ["python3", "-c", "print('Knowledge distill: student')"],       2, 5, "node-b", -800,  -798,    -796,  -500),
        ("genomics-bwa-align",     ["echo", "BWA genome alignment"],                               1, 2, "node-a", -700,  -699,    -698,  -500),
        ("genomics-variant-call",  ["echo", "GATK variant calling pipeline"],                      1, 2, "node-b", -600,  -599,    -598,  -400),
        ("astro-galaxy-classify",  ["python3", "-c", "print('Galaxy morphology classification')"], 1, 3, "node-a", -500,  -499,    -498,  -300),
        ("nlp-sentiment-train",    ["python3", "-c", "print('Sentiment analysis training')"],      1, 2, "node-b", -400,  -399,    -398,  -200),
        ("nlp-ner-train",          ["python3", "-c", "print('Named entity recognition')"],         1, 2, "node-a", -300,  -299,    -298,  -100),
    ]

    for job_id, cmd, gpus, prio, node, enq_o, place_o, run_o, done_o in done_jobs:
        spec = sql_str(make_spec(job_id, cmd, gpus, prio))
        timestamps = sql_str(json.dumps({
            "enqueued": ts(enq_o), "placed": ts(place_o),
            "running": ts(run_o), "done": ts(done_o),
        }))
        statements.append(
            f"INSERT INTO jobs (job_id, spec, status, node_id, gpu_ids, timestamps, exit_code) "
            f"VALUES ('{job_id}', '{spec}'::jsonb, 'DONE', '{node}', '{gpu_arr(gpus)}', "
            f"'{timestamps}'::jsonb, 0) ON CONFLICT (job_id) DO NOTHING;"
        )

    # 5 FAILED jobs
    failed_jobs = [
        ("ml-train-oom-large",   ["python3", "-c", "import sys; sys.exit(137)"], 4, "node-a", 137, "OOM killed by cgroup (exceeded 32GB GPU memory)",  -2550, -2548, -2546, -2500),
        ("sim-crash-segfault",   ["echo", "segfault simulation"],                2, "node-b", 139, "SIGSEGV: invalid memory access in CUDA kernel",    -1850, -1848, -1846, -1800),
        ("data-ingest-timeout",  ["echo", "data ingest"],                        1, "node-a", 1,   "Timeout: exceeded 3600s wall-clock limit",          -1450, -1449, -1448, -1400),
        ("ml-eval-nan-loss",     ["python3", "-c", "print(float('nan'))"],       1, "node-b", 1,   "Training diverged: NaN loss detected at epoch 12",  -750,  -749,  -748,  -700),
        ("render-gpu-ecc-error", ["echo", "render with ECC error"],              2, "node-a", 1,   "GPU ECC uncorrectable error on device 0",           -350,  -349,  -348,  -300),
    ]

    for job_id, cmd, gpus, node, exit_code, reason, enq_o, place_o, run_o, fail_o in failed_jobs:
        spec = sql_str(make_spec(job_id, cmd, gpus, 3))
        timestamps = sql_str(json.dumps({
            "enqueued": ts(enq_o), "placed": ts(place_o),
            "running": ts(run_o), "failed": ts(fail_o),
        }))
        reason_sql = sql_str(reason)
        statements.append(
            f"INSERT INTO jobs (job_id, spec, status, node_id, gpu_ids, timestamps, exit_code, reason) "
            f"VALUES ('{job_id}', '{spec}'::jsonb, 'FAILED', '{node}', '{gpu_arr(gpus)}', "
            f"'{timestamps}'::jsonb, {exit_code}, '{reason_sql}') ON CONFLICT (job_id) DO NOTHING;"
        )

    # 3 RUNNING jobs
    running_jobs = [
        ("ml-train-llama-7b",   ["python3", "-c", "print('Training LLaMA-7B')"],  4, "node-a", -180, -178, -175),
        ("sim-protein-fold",    ["python3", "-c", "print('Protein folding')"],     2, "node-b", -120, -118, -115),
        ("render-4k-animation", ["echo", "rendering 4K animation frames"],         2, "node-a", -60,  -58,  -55),
    ]

    for job_id, cmd, gpus, node, enq_o, place_o, run_o in running_jobs:
        spec = sql_str(make_spec(job_id, cmd, gpus, 7))
        timestamps = sql_str(json.dumps({
            "enqueued": ts(enq_o), "placed": ts(place_o), "running": ts(run_o),
        }))
        statements.append(
            f"INSERT INTO jobs (job_id, spec, status, node_id, gpu_ids, timestamps) "
            f"VALUES ('{job_id}', '{spec}'::jsonb, 'RUNNING', '{node}', '{gpu_arr(gpus)}', "
            f"'{timestamps}'::jsonb) ON CONFLICT (job_id) DO NOTHING;"
        )

    # Execute all SQL in one batch
    sql = "\n".join(statements)
    if not psql_exec(sql):
        print("ERROR: Failed to insert historical jobs.")
        sys.exit(1)

    print("  33 historical jobs inserted (25 DONE, 5 FAILED, 3 RUNNING).\n")

    # --- 3. Submit QUEUED jobs via API ---
    print("[3/5] Submitting 4 queued jobs via API...")

    queued_jobs = [
        ("ml-train-stable-diff",  ["python3", "-c", "print('Training Stable Diffusion v2')"], 4, 9),
        ("genomics-rna-seq",      ["echo", "RNA-seq differential expression analysis"],        1, 4),
        ("astro-lensing-sim",     ["echo", "Gravitational lensing simulation"],                2, 5),
        ("nlp-translation-en-de", ["python3", "-c", "print('EN->DE translation model')"],     2, 6),
    ]

    for job_id, cmd, gpus, prio in queued_jobs:
        api_post("jobs", {
            "job_id": job_id,
            "image": "",
            "cmd": cmd,
            "gpus": gpus,
            "priority": prio,
        })
        print(f"  submitted {job_id}")

    print("  4 QUEUED jobs submitted.\n")

    # --- 4. Set policy ---
    print("[4/5] Setting scheduler policy to ROUND_ROBIN...")
    api_put("policies/active", {"policy": "ROUND_ROBIN"})
    print("  Policy set to ROUND_ROBIN.\n")

    # --- 5. Verify ---
    print("[5/5] Verifying...")
    summary = api_get("jobs/summary")
    if summary:
        print(f"  Job summary: {json.dumps(summary)}")
    print()

    print("=== Demo data seeded successfully! ===")
    print()
    print("Summary:")
    print("  - 25 DONE jobs (ML, simulation, genomics, rendering)")
    print("  -  5 FAILED jobs (with realistic error reasons)")
    print("  -  3 RUNNING jobs (in-progress)")
    print("  -  4 QUEUED jobs (agents will process these live)")
    print("  - Policy: ROUND_ROBIN")
    print()
    print("Open http://localhost:5173 and enter 'local-operator-token' to explore.")


if __name__ == "__main__":
    main()
