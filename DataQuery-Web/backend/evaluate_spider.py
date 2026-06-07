"""
Spider dev-set evaluation for the DAIL-SQL demo.

Usage:
    python evaluate_spider.py                        # mock provider, 100 samples
    python evaluate_spider.py --provider deepseek    # call DeepSeek API
    python evaluate_spider.py --max-samples 200 --provider auto
    python evaluate_spider.py --output results.json  # also save per-sample results

Metrics:
    EM  — Exact Match (normalized SQL string comparison)
    EX  — Execution Accuracy (same result set on the SQLite database)
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.nl2sql import generate_sql
from modules.rag import spider_data_dir


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def load_spider_schemas(tables_path: Path) -> dict[str, dict[str, list[str]]]:
    """Return {db_id: {table: [col, ...]}} from Spider tables.json."""
    with tables_path.open(encoding="utf-8") as f:
        table_list = json.load(f)

    result: dict[str, dict[str, list[str]]] = {}
    for meta in table_list:
        db_id = meta.get("db_id", "")
        tables = meta.get("table_names_original") or meta.get("table_names") or []
        columns = meta.get("column_names_original") or meta.get("column_names") or []

        by_table: dict[int, list[str]] = {}
        for item in columns:
            if not isinstance(item, list) or len(item) < 2:
                continue
            table_idx, col_name = item[0], item[1]
            if table_idx < 0:
                continue
            by_table.setdefault(table_idx, []).append(str(col_name))

        schema: dict[str, list[str]] = {}
        for idx, table in enumerate(tables):
            schema[str(table)] = by_table.get(idx, [])

        result[db_id] = schema
    return result


# ---------------------------------------------------------------------------
# SQL normalization
# ---------------------------------------------------------------------------

_SPACES_RE = re.compile(r"\s+")


def normalize_sql(sql: str) -> str:
    sql = sql.strip().rstrip(";").lower()
    sql = _SPACES_RE.sub(" ", sql)
    return sql


# ---------------------------------------------------------------------------
# Execution accuracy
# ---------------------------------------------------------------------------

def _run_sql(db_path: Path, sql: str) -> list | None:
    try:
        conn = sqlite3.connect(str(db_path))
        conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        conn.close()
        return sorted(str(r) for r in rows)
    except Exception:
        return None


def execution_match(db_path: Path, gold_sql: str, gen_sql: str) -> bool:
    if not db_path.exists():
        return False
    gold = _run_sql(db_path, gold_sql)
    gen = _run_sql(db_path, gen_sql)
    return gold is not None and gen is not None and gold == gen


# ---------------------------------------------------------------------------
# Main evaluation loop
# ---------------------------------------------------------------------------

def evaluate(
    max_samples: int = 100,
    provider: str = "mock",
    output_path: Path | None = None,
    verbose: bool = False,
) -> None:
    base = spider_data_dir()
    dev_path = base / "dev.json"
    tables_path = base / "tables.json"

    if not dev_path.exists():
        print(f"[ERROR] Spider dev.json not found at: {dev_path}")
        return
    if not tables_path.exists():
        print(f"[ERROR] Spider tables.json not found at: {tables_path}")
        return

    print(f"Loading Spider dev set from: {base}")
    schemas = load_spider_schemas(tables_path)

    with dev_path.open(encoding="utf-8") as f:
        dev_data = json.load(f)

    samples = dev_data[:max_samples]
    print(f"Evaluating {len(samples)} samples  (provider={provider})\n")

    exact_match = 0
    exec_match = 0
    exec_possible = 0
    errors = 0
    total = 0
    per_sample: list[dict] = []

    for i, sample in enumerate(samples):
        db_id = sample.get("db_id", "")
        question = sample.get("question", "")
        gold_sql = sample.get("query", "").strip()
        schema = schemas.get(db_id)

        if not schema:
            if verbose:
                print(f"[{i+1}] SKIP — no schema for db_id={db_id!r}")
            errors += 1
            continue

        try:
            gen_sql, *_ = generate_sql(
                question=question,
                schema=schema,
                provider=provider,
                language="en",
                task_mode="spider",
            )
        except Exception as exc:
            if verbose:
                print(f"[{i+1}] ERROR — {exc}")
            errors += 1
            continue

        total += 1
        em = normalize_sql(gen_sql) == normalize_sql(gold_sql)
        if em:
            exact_match += 1

        db_path = base / "database" / db_id / f"{db_id}.sqlite"
        ex = execution_match(db_path, gold_sql, gen_sql)
        if db_path.exists():
            exec_possible += 1
            if ex:
                exec_match += 1

        per_sample.append({
            "index": i,
            "db_id": db_id,
            "question": question,
            "gold_sql": gold_sql,
            "gen_sql": gen_sql,
            "exact_match": em,
            "exec_match": ex,
        })

        if verbose or (i + 1) % 20 == 0:
            em_rate = exact_match / total * 100
            ex_rate = exec_match / max(1, exec_possible) * 100
            print(f"[{i+1:4d}/{len(samples)}]  EM={exact_match}/{total} ({em_rate:.1f}%)  "
                  f"EX={exec_match}/{exec_possible} ({ex_rate:.1f}%)")

    # Summary
    print("\n" + "=" * 55)
    print(f"  Samples evaluated : {total}")
    print(f"  Errors / skipped  : {errors}")
    print(f"  Exact Match (EM)  : {exact_match}/{total} = {exact_match/max(1,total)*100:.2f}%")
    if exec_possible:
        print(f"  Execution Acc (EX): {exec_match}/{exec_possible} = {exec_match/max(1,exec_possible)*100:.2f}%")
    else:
        print("  Execution Acc (EX): no SQLite databases found")
    print("=" * 55)

    if output_path:
        output_path.write_text(
            json.dumps(
                {
                    "provider": provider,
                    "total": total,
                    "errors": errors,
                    "exact_match": exact_match,
                    "exec_match": exec_match,
                    "exec_possible": exec_possible,
                    "em_rate": exact_match / max(1, total),
                    "ex_rate": exec_match / max(1, exec_possible) if exec_possible else None,
                    "samples": per_sample,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nResults saved to: {output_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate demo on Spider dev set (DAIL-SQL style)")
    parser.add_argument("--max-samples", type=int, default=100, help="Number of dev samples to evaluate (default 100)")
    parser.add_argument("--provider", default="mock", choices=["auto", "deepseek", "ollama", "mock"],
                        help="LLM provider to use (default mock — uses rule-based SQL)")
    parser.add_argument("--output", type=str, default=None, help="Path to save JSON results")
    parser.add_argument("--verbose", action="store_true", help="Print result for every sample")
    args = parser.parse_args()

    evaluate(
        max_samples=args.max_samples,
        provider=args.provider,
        output_path=Path(args.output) if args.output else None,
        verbose=args.verbose,
    )
