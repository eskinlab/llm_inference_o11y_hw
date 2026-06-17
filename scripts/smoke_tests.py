"""Phase 4 smoke test — fire 10 questions at the agent and print results.

Mix: 4 easy (should pass on iter 1), 6 hard (likely to trigger revise loop).
Run:
    uv run python scripts/smoke_phase4.py
    uv run python scripts/smoke_phase4.py --url http://localhost:8001/answer
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

QUESTIONS = [
    # --- Easy: straightforward joins, single condition ---
    {
        "label": "[easy] Ajax superpowers",
        "question": "List down Ajax's superpowers.",
        "db": "superhero",
    },
    {
        "label": "[easy] Super Strength count",
        "question": 'How many superheroes have the super power of "Super Strength"?',
        "db": "superhero",
    },
    {
        "label": "[easy] Commentator badges 2014",
        "question": "How many users received commentator badges in 2014?",
        "db": "codebase_community",
    },
    {
        "label": "[easy] Ancestor's Chosen type",
        "question": "What is the type of the card \"Ancestor's Chosen\" as originally printed?",
        "db": "card_games",
    },
    # --- Medium: multi-table joins, aggregations ---
    {
        "label": "[medium] Australian GP circuit coords",
        "question": "What is the coordinates location of the circuits for Australian grand prix?",
        "db": "formula_1",
    },
    {
        "label": "[medium] Male clients in Praha",
        "question": "How many male clients in 'Hl.m. Praha' district?",
        "db": "financial",
    },
    {
        "label": "[medium] Top 5 schools by enrollment",
        "question": "List the top five schools, by descending order, from the highest to the lowest, the most number of Enrollment (Ages 5-17). Please give their NCES school identification number.",
        "db": "california_schools",
    },
    # --- Hard: complex logic, subqueries, likely to trigger revise ---
    {
        "label": "[hard] Average crimes 1995 + account filter",
        "question": "What is the average number of crimes committed in 1995 in regions where the number exceeds 4000 and the region has accounts that are opened starting from the year 1997?",
        "db": "financial",
    },
    {
        "label": "[hard] Lewis Hamilton avg fastest lap (time string math)",
        "question": "What is the average fastest lap time in seconds for Lewis Hamilton in all the Formula_1 races?",
        "db": "formula_1",
    },
    {
        "label": "[hard] Carcinogenic molecules with Chlorine %",
        "question": "Calculate the percentage of carcinogenic molecules which contain the Chlorine element.",
        "db": "toxicology",
    },
]


def fire(url: str, q: dict) -> dict:
    payload = {
        "question": q["question"],
        "db": q["db"],
        "tags": {"phase": "phase4_smoke"},
    }
    t0 = time.monotonic()
    resp = httpx.post(url, json=payload, timeout=120.0)
    elapsed = time.monotonic() - t0
    resp.raise_for_status()
    data = resp.json()
    return {**data, "_elapsed": round(elapsed, 2)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8001/answer")
    args = p.parse_args()

    print(f"Firing {len(QUESTIONS)} questions at {args.url}\n{'=' * 60}")

    triggered_revise = 0
    failed = 0

    for i, q in enumerate(QUESTIONS, 1):
        print(f"\n[{i:02d}/10] {q['label']}")
        print(f"       DB: {q['db']}")
        try:
            r = fire(args.url, q)
        except Exception as e:
            print(f"       ERROR: {e}")
            failed += 1
            continue

        iters = r.get("iterations", 0)
        ok = r.get("ok", False)
        elapsed = r.get("_elapsed", 0)
        sql = r.get("sql", "")[:80].replace("\n", " ")

        status = "OK" if ok else f"FAIL ({r.get('error', '?')})"
        revise_flag = " *** REVISE TRIGGERED" if iters > 1 else ""
        print(f"       status={status}  iters={iters}  time={elapsed}s{revise_flag}")
        print(f"       sql: {sql}")

        if iters > 1:
            triggered_revise += 1

    print(f"\n{'=' * 60}")
    print(f"Done. Revise triggered: {triggered_revise}/10   Errors: {failed}/10")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
