# Project Demo Runbook

This is a concrete end-to-end demo flow for running the app on an SJSU-style SLURM cluster from a terminal.

Replace these placeholders before running commands:

- `<your-id>`: your SJSU/HPC username
- `<hpc-login-node>`: the SSH host you use to access the cluster
- `<login-node>`: the hostname where you run the control plane
- `<operator-token>`: bearer token used for operator API calls
- `<agent-token>`: bearer token used by SLURM job callbacks

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
export OPERATOR_API_TOKEN="<operator-token>"
export AGENT_API_TOKEN="<agent-token>"
export CONTROL_PLANE_CALLBACK_URL="http://<login-node>:8000"
export SLURM_DEFAULT_PARTITION="gpuqs"
export SLURM_POLL_INTERVAL_SECS=10

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

Submit a job through the control plane:

```bash
curl -X POST http://<login-node>:8000/api/jobs \
  -H "Authorization: Bearer <operator-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "demo-001",
    "image": "",
    "cmd": ["nvidia-smi"],
    "gpus": 1,
    "metadata": {"partition": "gpuqs"}
  }'
```

## 5. Verify the job

Check the control-plane view:

```bash
curl http://<login-node>:8000/api/jobs | python3 -m json.tool
```

Check the direct SLURM view:

```bash
squeue -u <your-id>
```

If the job finishes quickly, inspect accounting output:

```bash
sacct -j <slurm-job-id> --format=JobID,State,ExitCode,NodeList
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
- Show the job appear in `/api/jobs`.
- Show the corresponding SLURM job in `squeue`.
- Show the state transition to `RUNNING` and then `DONE` or `FAILED`.
- Show the Nodes and Metrics pages in the frontend.

## Notes

- `CONTROL_PLANE_CALLBACK_URL` must be reachable from compute nodes. `127.0.0.1` is only valid if the callback runs on the same host namespace as the job.
- If your cluster uses a different Python module name/version, replace `python3/3.11.5` with the correct one.
- If your cluster uses different partitions, replace `gpuqs` with a valid partition name.
