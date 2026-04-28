# Project Demo Runbook

This is a concrete end-to-end demo flow for running the app on an SJSU-style SLURM cluster from a terminal.

This document now includes two valid demo paths:

1. operator/admin token demo
2. response-mode user token demo for HPC environments where SMTP is unavailable

Replace these placeholders before running commands:

- `<your-id>`: your SJSU/HPC username
- `<hpc-login-node>`: the SSH host you use to access the cluster
- `<login-node>`: the hostname where you run the control plane
- `<operator-token>`: bearer token used for operator API calls
- `<agent-token>`: bearer token used by SLURM job callbacks
- `<user-token>`: bearer token returned from admin approval when `TOKEN_DELIVERY_MODE=response`

## 1. SSH to the cluster

From your local machine:

```bash
ssh <your-id>@<hpc-login-node>
```

## 2. Prepare the project on the login node

If the repo is not already on the cluster, copy it from your local machine:

```bash
rsync -avz \
  --exclude='.venv' --exclude='node_modules' --exclude='.git' \
  --exclude='__pycache__' --exclude='frontend/dist' --exclude='*.pyc' \
  ~/Projects/CudaAwareDistributedScheduler <your-id>@<hpc-login-node>:~/
```

Then on the cluster:

```bash
module load python3/3.11.5
cd ~/CudaAwareDistributedScheduler
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Start the control plane

On the login node:

```bash
cd ~/CudaAwareDistributedScheduler
source .venv/bin/activate

export BACKEND=slurm
export DATABASE_URL="sqlite:////home/<your-id>/scheduler.db"
export QUEUE_BACKEND=memory
export AUTH_MODE=token
export ADMIN_API_TOKEN="<operator-token>"
export AGENT_API_TOKEN="<agent-token>"
export CONTROL_PLANE_CALLBACK_URL="http://<login-node>:8000"
export SLURM_DEFAULT_PARTITION="gpuqs"
export SLURM_POLL_INTERVAL_SECS=10
export TOKEN_DELIVERY_MODE="response"

python3 -m uvicorn control_plane.app:app --host 0.0.0.0 --port 8000
```

Expected output includes:

```text
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

## 4. Submit a demo job

Open a second terminal and SSH to the same cluster:

```bash
ssh <your-id>@<hpc-login-node>
cd ~/CudaAwareDistributedScheduler
source .venv/bin/activate
```

### Option A: Submit directly with the bootstrap admin token

Submit a job through the control plane:

```bash
curl -X POST http://<login-node>:8000/api/jobs \
  -H "Authorization: Bearer <operator-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "demo-001",
    "project": "default",
    "image": "",
    "cmd": ["sh", "-c", "sleep 5; hostname"],
    "gpus": 1,
    "metadata": {"partition": "gpuqs"}
  }'
```

### Option B: Demo the token-request workflow first

Submit a public token request:

```bash
curl -X POST http://<login-node>:8000/api/token-requests \
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
curl -H "Authorization: Bearer <operator-token>" \
  "http://<login-node>:8000/api/admin/token-requests?status=PENDING"
```

Approve a pending request and capture the returned token:

```bash
REQ_ID="<request-id>"

curl -X POST \
  "http://<login-node>:8000/api/admin/token-requests/$REQ_ID/approve" \
  -H "Authorization: Bearer <operator-token>" \
  -H "Content-Type: application/json" \
  -d '{"review_notes":"approved during live demo"}'
```

When `TOKEN_DELIVERY_MODE=response`, the approval response contains:

```json
{
  "plaintext_token": "<user-token>"
}
```

Use that returned user token to submit the demo job:

```bash
curl -X POST http://<login-node>:8000/api/jobs \
  -H "Authorization: Bearer <user-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "demo-user-001",
    "project": "default",
    "image": "",
    "cmd": ["sh", "-c", "sleep 5; hostname"],
    "gpus": 1,
    "metadata": {"partition": "gpuqs"}
  }'
```

## 5. Verify the job

Check the control-plane view:

```bash
curl -H "Authorization: Bearer <operator-token>" \
  http://<login-node>:8000/api/jobs | python3 -m json.tool
```

Check the direct SLURM view:

```bash
squeue -u <your-id>
```

If you want to show a user-scoped read:

```bash
curl -H "Authorization: Bearer <user-token>" \
  http://<login-node>:8000/api/jobs/demo-user-001 | python3 -m json.tool
```

## 6. View the dashboard from your laptop

From your local machine, tunnel the control-plane port:

```bash
ssh -L 8000:<login-node>:8000 <your-id>@<hpc-login-node>
```

Then in a local terminal:

```bash
cd ~/Projects/CudaAwareDistributedScheduler/frontend
npm install
echo 'VITE_API_BASE=http://localhost:8000' > .env
npm run dev
```

Open:

- `http://localhost:5173`

## 7. What to point out during the demo

- Submit a job from the API or UI.
- Optionally show the token request and approval flow before job submission.
- Show the job appear in `/api/jobs`.
- Show the corresponding SLURM job in `squeue`.
- Show the state transition to `RUNNING` and then `DONE` or `FAILED`.
- Show the Nodes and Metrics pages in the frontend.

## Notes

- `CONTROL_PLANE_CALLBACK_URL` must be reachable from compute nodes. `127.0.0.1` is only valid if the callback runs on the same host namespace as the job.
- If your cluster uses a different Python module name/version, replace `python3/3.11.5` with the correct one.
- If your cluster uses different partitions, replace `gpuqs` with a valid partition name.
- `TOKEN_DELIVERY_MODE=response` is recommended on HPC when outbound SMTP is blocked.
- On clusters where `sacct` is disabled, lifecycle completion can still succeed through callback updates as long as `CONTROL_PLANE_CALLBACK_URL` is reachable from compute nodes.
