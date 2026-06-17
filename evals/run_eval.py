"""Eval runner using execution accuracy.

Reads evals/eval_set.jsonl, calls the agent at AGENT_URL on each question,
then compares the agent's SQL output to the gold SQL by *executed rows*
(canonicalized: sorted, stringified, None-coerced to empty).

Helpers (run_sql / canonicalize / matches) are provided. You implement
eval_one() and summarize().

Run:
    uv run python evals/run_eval.py --out results/eval_baseline.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVAL_FILE = ROOT / "evals" / "eval_set.jsonl"
DEFAULT_OUT_FILE = ROOT / "results" / "eval_baseline.json"
DB_DIR = ROOT / "data" / "bird"
AGENT_URL_DEFAULT = "http://localhost:8001/answer"
# Must match MAX_ITERATIONS in agent/graph.py
_MAX_ITERS = 3


# ---------- Helpers (provided) -----------------------------------------

def run_sql(db_id: str, sql: str, timeout: float = 5.0) -> tuple[bool, list[tuple] | None, str | None]:
    """Run sql against db_id in read-only mode. Returns (ok, rows, error)."""
    path = DB_DIR / f"{db_id}.sqlite"
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout) as conn:
            cur = conn.execute(sql)
            rows = cur.fetchall()
            return True, rows, None
    except Exception as e:  # noqa: BLE001
        return False, None, f"{type(e).__name__}: {e}"


def canonicalize(rows: list[tuple] | None) -> list[tuple] | None:
    """Sort rows; coerce cells to str; None -> ''."""
    if rows is None:
        return None
    return sorted(tuple("" if c is None else str(c) for c in row) for row in rows)


def matches(gold_rows: list[tuple] | None, pred_rows: list[tuple] | None) -> bool:
    if gold_rows is None or pred_rows is None:
        return False
    return canonicalize(gold_rows) == canonicalize(pred_rows)


# ---------- Implement these (Phase 5) ----------------------------------

def eval_one(question: dict, agent_url: str) -> dict:
    """Score one question. Return a dict capturing per-iteration correctness."""
    q_text = question["question"]
    db_id = question["db_id"]
    gold_sql = question["gold_sql"]

    _, gold_rows, _ = run_sql(db_id, gold_sql)

    try:
        resp = httpx.post(agent_url, json={"question": q_text, "db": db_id}, timeout=120.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        return {
            "question": q_text,
            "db_id": db_id,
            "gold_sql": gold_sql,
            "agent_sql": "",
            "agent_ok": False,
            "agent_error": str(e),
            "iterations": 0,
            "iter_correct": {str(k): False for k in range(_MAX_ITERS)},
            "correct": False,
        }

    history = data.get("history", [])
    sqls = [h["sql"] for h in history if "sql" in h]

    # For each checkpoint k, run the SQL the agent had at that point.
    # If the agent stopped before checkpoint k, carry forward the last result.
    iter_correct: dict[str, bool] = {}
    last_correct = False
    for k in range(_MAX_ITERS):
        if k < len(sqls):
            _, pred_rows, _ = run_sql(db_id, sqls[k])
            last_correct = matches(gold_rows, pred_rows)
        iter_correct[str(k)] = last_correct

    # "correct" = what the agent actually served (the final history entry).
    correct = iter_correct[str(len(sqls) - 1)] if sqls else False

    return {
        "question": q_text,
        "db_id": db_id,
        "gold_sql": gold_sql,
        "agent_sql": data.get("sql", ""),
        "agent_ok": data.get("ok", False),
        "agent_error": data.get("error"),
        "iterations": data.get("iterations", 0),
        "iter_correct": iter_correct,
        "correct": correct,
    }


def summarize(results: list[dict]) -> dict:
    """Aggregate per-question results.

    Per-iteration carry-forward: if the agent terminated at iteration j < k
    (verify said ok at j, or it hit MAX_ITERATIONS at j < k), treat the
    question's iteration-k result as identical to its iteration-j result.
    The agent stopped emitting; whatever it had at termination is what
    would have been served had we polled at iteration k.
    """
    n = len(results)
    if n == 0:
        return {"n": 0, "overall_pass_rate": 0.0, "per_iter_pass_rate": {}, "mean_iterations": 0.0}

    per_iter_pass_rate: dict[str, float] = {}
    for k in range(_MAX_ITERS):
        key = str(k)
        count = sum(1 for r in results if r.get("iter_correct", {}).get(key, False))
        per_iter_pass_rate[key] = round(count / n, 4)

    overall_correct = sum(1 for r in results if r.get("correct", False))
    mean_iters = sum(r.get("iterations", 0) for r in results) / n

    return {
        "n": n,
        "overall_pass_rate": round(overall_correct / n, 4),
        "per_iter_pass_rate": per_iter_pass_rate,
        "mean_iterations": round(mean_iters, 2),
    }


# ---------- Main (provided) --------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-set", type=Path, default=DEFAULT_EVAL_FILE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_FILE)
    parser.add_argument("--agent-url", default=AGENT_URL_DEFAULT)
    args = parser.parse_args()

    questions = [json.loads(line) for line in args.eval_set.read_text().splitlines() if line.strip()]
    print(f"Loaded {len(questions)} eval questions from {args.eval_set}")

    results: list[dict] = []
    t0 = time.monotonic()
    for i, q in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] {q['db_id']}: {q['question'][:60]}...", flush=True)
        results.append(eval_one(q, args.agent_url))
    elapsed = time.monotonic() - t0

    summary = summarize(results)
    out = {
        "summary": summary,
        "wall_clock_seconds": elapsed,
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2))
    print(f"Wrote {args.out}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
