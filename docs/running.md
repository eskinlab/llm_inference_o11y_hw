# Running the Environment

## Prerequisites

- WSL2 with Docker Desktop integration enabled
- Python 3.12 via `uv`
- `.env` configured (copy from `.env.example`)

---

## 1. Start Docker (o11y stack)

Run from the project root inside **WSL2**:

```bash
docker compose up -d
```

Verify all containers are healthy:

```bash
docker compose ps
```

Expected services: Prometheus, Grafana, Postgres, ClickHouse, Redis, MinIO, Langfuse-worker, Langfuse-web.

| UI | URL | Credentials |
|----|-----|-------------|
| Prometheus | http://localhost:9090 | — |
| Grafana | http://localhost:3000 | admin / admin |
| Langfuse | http://localhost:3001 | your account |

---

## 2. Start the Agent Server

Open a **WSL2** terminal in the project root and activate the virtual environment:

```bash
source .venv/bin/activate
```

Then start the server:

```bash
uvicorn agent.server:app --host 0.0.0.0 --port 8001
```

Expected output:

```
INFO:     Started server process [xxxx]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8001 (Press CTRL+C to quit)
```

> **Note:** First startup takes ~1 minute while the Langfuse callback handler connects.

---

## 3. Health Check

In a second WSL2 terminal:

```bash
curl http://localhost:8001/health
```

Expected response:

```json
{"status": "ok"}
```

---

## 4. Test a Query

```bash
curl -X POST http://localhost:8001/answer \
  -H "Content-Type: application/json" \
  -d '{"question": "How many constructors are there?", "db": "formula_1"}'
```

Expected response shape:

```json
{
  "sql": "SELECT COUNT(*) ...",
  "rows": [[208]],
  "iterations": 1,
  "ok": true,
  "error": null,
  "history": [...]
}
```

---

## 5. Smoke Test (10 questions)

```bash
python scripts/smoke_test.py
```

All 10 should return `ok=true`. Expect `iterations > 1` on complex aggregation or time-conversion queries.

---

## Stopping

```bash
# Stop the agent server
Ctrl+C

# Stop Docker stack
docker compose down
```
