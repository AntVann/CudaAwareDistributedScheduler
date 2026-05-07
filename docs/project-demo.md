# Project Demo Runbook

This is the current end-to-end demo flow for running `cudaScheduler` against the SJSU COE HPC SLURM cluster from a laptop.

The working cluster path is:

1. SSH to `coe-hpc1.sjsu.edu`
2. SSH from there to `coe-hpc3` / `g17`
3. Run the control plane on `g17`
4. Tunnel laptop `localhost:8088` to `coe-hpc3:8088`
5. Run the React UI locally at `localhost:5173`

## Per-User Placeholders

Replace these values before running commands:

- `<sjsu-id>`: your SJSU/HPC username, for example `018183828`
- `<repo-url>`: the Git remote URL for this repository
- `<operator-token>`: a random admin/operator bearer token
- `<agent-token>`: a different random callback/agent bearer token

Generate tokens once per user:

```bash
openssl rand -hex 32
openssl rand -hex 32
```

Do not commit tokens. Store them in `~/.scheduler-env` on HPC and in a local `.env` file on your laptop.

## Required Terminals

You need three terminals for a normal demo.

| #  | Where  | Purpose |
|----|--------|---------|
| T1 | HPC/g17 | Control plane (`uvicorn`) |
| T3 | Laptop | SSH tunnel from laptop to `g17:8088` |
| T4 | Laptop | Vite frontend (`localhost:5173`) |

Two more are useful but optional.

| #  | Where  | Purpose |
|----|--------|---------|
| T2 | HPC/g17 | `sinfo`, `squeue`, `sacct`, logs, SQLite inspection |
| T5 | Laptop | `rsync`, `curl`, `git`, ad-hoc checks |

## One-Time HPC Setup

Run this per HPC account.

```bash
ssh <sjsu-id>@coe-hpc1.sjsu.edu
ssh coe-hpc3
```

If the repository is not present on `g17`:

```bash
cd ~
git clone <repo-url> CudaAwareDistributedScheduler
cd ~/CudaAwareDistributedScheduler
```

Create the persistent token file:

```bash
cat > ~/.scheduler-env <<'EOF'
export OPERATOR_API_TOKEN=<operator-token>
export AGENT_API_TOKEN=<agent-token>
EOF
chmod 600 ~/.scheduler-env
```

Create user-owned SLURM script/log directories:

```bash
mkdir -p ~/scheduler-scripts ~/scheduler-logs
chmod 700 ~/scheduler-scripts ~/scheduler-logs
```

Create the Python 3.9 virtualenv on `g17`:

```bash
cd ~/CudaAwareDistributedScheduler
python3 -m venv .venv-g17
source .venv-g17/bin/activate
```

If `g17` cannot reach PyPI, copy `wheelhouse-g17.tgz` from a machine that has it, then install offline:

```bash
tar xzf wheelhouse-g17.tgz
python -m pip install --no-index --find-links ./wheelhouse-g17 -r requirements.txt
python -m pip install --no-index --find-links ./wheelhouse-g17 eval_type_backport
```

If PyPI access works, this is enough:

```bash
python -m pip install -r requirements.txt
python -m pip install eval_type_backport
```

## One-Time Laptop Setup

Clone the repo on the laptop:

```bash
cd ~/Projects
git clone <repo-url> CudaAwareDistributedScheduler
cd ~/Projects/CudaAwareDistributedScheduler
```

Create local `.env` with the same tokens as the HPC `~/.scheduler-env`:

```bash
cat > .env <<'EOF'
export OPERATOR_API_TOKEN=<operator-token>
export AGENT_API_TOKEN=<agent-token>
EOF
chmod 600 .env
```

Install frontend dependencies:

```bash
cd ~/Projects/CudaAwareDistributedScheduler/frontend
npm install
```

Use Node `20.19+` or `22.12+` for Vite. Older Node may warn even if the build still works.

## T1 - HPC/g17 Control Plane

```bash
ssh <sjsu-id>@coe-hpc1.sjsu.edu
ssh coe-hpc3

cd ~/CudaAwareDistributedScheduler
source .venv-g17/bin/activate
source ~/.scheduler-env

mkdir -p ~/scheduler-scripts ~/scheduler-logs
chmod 700 ~/scheduler-scripts ~/scheduler-logs

export BACKEND=slurm
export DATABASE_URL="sqlite:////home/<sjsu-id>/scheduler.db"
export QUEUE_BACKEND=memory
export AUTH_MODE=token
export ADMIN_API_TOKEN="$OPERATOR_API_TOKEN"
export CONTROL_PLANE_CALLBACK_URL="http://g17:8088"
export SLURM_DEFAULT_PARTITION=gpuqs
export SLURM_SCRIPT_DIR="$HOME/scheduler-scripts"
export SLURM_LOG_DIR="$HOME/scheduler-logs"
export SLURM_POLL_INTERVAL_SECS=10
export TOKEN_DELIVERY_MODE=response

python -m uvicorn control_plane.app:app --host 0.0.0.0 --port 8088
```

Expected output:

```text
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8088
```

Leave this terminal running. If you stop `uvicorn` with `Ctrl-C`, the exported env vars stay in the same shell. If you open a new shell, source/export them again.

## T2 - HPC/g17 Inspection

```bash
ssh <sjsu-id>@coe-hpc1.sjsu.edu
ssh coe-hpc3
cd ~/CudaAwareDistributedScheduler
```

Useful commands:

```bash
sinfo
sinfo -p gpuqs -o "%P %a %D %T %N"
squeue -u <sjsu-id>
sacct -j <slurm_id> --format=JobIDRaw,State,ExitCode,Start,End,NodeList
tail -f ~/scheduler-logs/<job_id>-*.err
sqlite3 ~/scheduler.db "SELECT job_id, status FROM jobs ORDER BY rowid DESC LIMIT 10;"
```

## T3 - Laptop SSH Tunnel

```bash
ssh -N -o ServerAliveInterval=30 \
  -L 8088:coe-hpc3:8088 \
  <sjsu-id>@coe-hpc1.sjsu.edu
```

This terminal has no prompt while the tunnel is active. Leave it open.

Verify from T5:

```bash
curl -i http://localhost:8088/health
```

## T4 - Laptop Frontend

```bash
cd ~/Projects/CudaAwareDistributedScheduler/frontend
npm run dev
```

Open:

```text
http://localhost:5173
```

Paste `<operator-token>` into the **API Token** box in the sidebar. The sidebar should show `bootstrap-admin`.

## T5 - Laptop Ad Hoc

```bash
cd ~/Projects/CudaAwareDistributedScheduler
source .env
```

Health check:

```bash
curl -i http://localhost:8088/health
```

Fast job summary:

```bash
curl -s -H "Authorization: Bearer $OPERATOR_API_TOKEN" \
  http://localhost:8088/api/jobs/summary | python3 -m json.tool
```

Full jobs list:

```bash
curl -s -H "Authorization: Bearer $OPERATOR_API_TOKEN" \
  http://localhost:8088/api/jobs | python3 -m json.tool
```

Prefer `/api/jobs/summary` for quick checks. `/api/jobs` can be slow because it may include full placement diagnostics.

Rsync backend changes to HPC:

```bash
rsync -avz \
  --exclude='.venv' --exclude='.venv-g17' --exclude='node_modules' --exclude='.git' \
  --exclude='__pycache__' --exclude='frontend/dist' --exclude='*.pyc' \
  ./control_plane ./agent \
  <sjsu-id>@coe-hpc1.sjsu.edu:~/CudaAwareDistributedScheduler/
```

After rsync, restart T1 with `Ctrl-C` and rerun the `python -m uvicorn ...` command.

## Submit Demo Jobs

From the UI:

1. Open `http://localhost:5173`
2. Paste the operator token if the sidebar is not signed in
3. Go to **Jobs**
4. Click **Submit Job**
5. Use this smoke-test command:

```json
["sh", "-c", "echo job=$SLURM_JOB_ID; hostname; nvidia-smi -L || true; sleep 5"]
```

Set:

- Project: `default`
- GPUs: `1`
- Partition: `Auto-select` or `gpuqs`

This is a lightweight SLURM/GPU-placement smoke test. It requests one GPU, prints node/GPU information, sleeps briefly, and exits.

From T5 with curl:

```bash
JOB_ID="demo-$(date +%s)"

curl -s -X POST http://localhost:8088/api/jobs \
  -H "Authorization: Bearer $OPERATOR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"job_id\": \"$JOB_ID\",
    \"project\": \"default\",
    \"image\": \"\",
    \"cmd\": [\"sh\", \"-c\", \"echo job=$JOB_ID; hostname; nvidia-smi -L || true; sleep 5\"],
    \"gpus\": 1,
    \"metadata\": {\"partition\": \"gpuqs\"}
  }" | python3 -m json.tool
```

Verify:

```bash
curl -s -H "Authorization: Bearer $OPERATOR_API_TOKEN" \
  http://localhost:8088/api/jobs/summary | python3 -m json.tool
```

## Token Request Demo

Use this only if you want to demonstrate the token approval flow. `TOKEN_DELIVERY_MODE=response` returns the plaintext token in the admin approval response, which is useful on HPC when outbound SMTP is unavailable.

Submit a public token request:

```bash
curl -X POST http://localhost:8088/api/token-requests \
  -H "Content-Type: application/json" \
  -d '{
    "subject_name": "demo-user",
    "email": "demo-user@example.com",
    "requested_projects": ["default"],
    "purpose": "SLURM response-mode demo"
  }'
```

List pending requests as admin:

```bash
curl -H "Authorization: Bearer $OPERATOR_API_TOKEN" \
  "http://localhost:8088/api/admin/token-requests?status=PENDING"
```

Approve:

```bash
REQ_ID="<request-id>"

curl -X POST \
  "http://localhost:8088/api/admin/token-requests/$REQ_ID/approve" \
  -H "Authorization: Bearer $OPERATOR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"review_notes":"approved during live demo"}'
```

Use the returned `plaintext_token` as a user token for project-scoped job submission.

## Coworker Setup

Each coworker uses their own SJSU id, home directory, database, script/log directories, and tokens.

For coworker `018183899`, change:

```bash
ssh 018183899@coe-hpc1.sjsu.edu
export DATABASE_URL="sqlite:////home/018183899/scheduler.db"
export SLURM_SCRIPT_DIR="/home/018183899/scheduler-scripts"
export SLURM_LOG_DIR="/home/018183899/scheduler-logs"
```

Their tunnel also uses their username:

```bash
ssh -N -o ServerAliveInterval=30 \
  -L 8088:coe-hpc3:8088 \
  018183899@coe-hpc1.sjsu.edu
```

They must create their own `~/.scheduler-env` on HPC:

```bash
cat > ~/.scheduler-env <<'EOF'
export OPERATOR_API_TOKEN=<coworker-operator-token>
export AGENT_API_TOKEN=<coworker-agent-token>
EOF
chmod 600 ~/.scheduler-env
```

They should also create their own local laptop `.env` with the same two tokens.

Do not share `scheduler.db`, `~/scheduler-scripts`, `~/scheduler-logs`, or bearer tokens between users.

## Notes

- Use `coe-hpc3` / `g17` for actual SLURM jobs. `coe-hpc1` is only the SSH entry point.
- Do not use `.venv` on `g17`; use `.venv-g17`.
- `ADMIN_API_TOKEN` should be set to `$OPERATOR_API_TOKEN` for bootstrap admin auth.
- `OPERATOR_API_TOKEN` and `AGENT_API_TOKEN` must be different tokens.
- `CONTROL_PLANE_CALLBACK_URL` must be reachable from SLURM jobs. For this cluster, use `http://g17:8088`.
- `SLURM_SCRIPT_DIR` and `SLURM_LOG_DIR` should be under your home directory, not `/tmp`, to avoid permission issues.
- If the dashboard shows zeros, confirm the sidebar is signed in as `bootstrap-admin`.
- If the frontend warns about Node, upgrade to Node `20.19+` or `22.12+`.
