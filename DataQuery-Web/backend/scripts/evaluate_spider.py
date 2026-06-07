import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from modules.nl2sql import generate_sql  # noqa: E402
from modules.numeric import parse_number  # noqa: E402
from modules.rag import spider_data_dir  # noqa: E402
from modules.semantic_schema import infer_semantic_schema  # noqa: E402


DEFAULT_REPORT_DIR = BACKEND_DIR / "eval_reports" / "spider"


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _schema_from_table_meta(table_meta: dict[str, Any]) -> dict[str, list[str]]:
    table_names = table_meta.get("table_names_original") or table_meta.get("table_names") or []
    column_names = table_meta.get("column_names_original") or table_meta.get("column_names") or []
    schema = {str(table): [] for table in table_names}
    for item in column_names:
        if not isinstance(item, list) or len(item) < 2:
            continue
        table_idx, column_name = item[0], item[1]
        if table_idx < 0 or table_idx >= len(table_names) or column_name == "*":
            continue
        schema[str(table_names[table_idx])].append(str(column_name))
    return schema


def load_table_metadata(spider_dir: Path) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for name in ["tables.json", "test_tables.json"]:
        path = spider_dir / name
        if not path.exists():
            continue
        for item in _load_json(path):
            metadata[str(item["db_id"])] = item
    return metadata


def load_spider_cases(spider_dir: Path, split: str) -> list[dict[str, Any]]:
    path = spider_dir / f"{split}.json"
    if not path.exists():
        raise FileNotFoundError(f"Spider split file not found: {path}")
    rows = _load_json(path)
    if not isinstance(rows, list):
        raise ValueError(f"Expected list in {path}")
    return rows


def sqlite_path_for_case(spider_dir: Path, db_id: str) -> Path:
    candidates = [
        spider_dir / "database" / db_id / f"{db_id}.sqlite",
        spider_dir / "test_database" / db_id / f"{db_id}.sqlite",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"SQLite DB not found for db_id={db_id}")


def _is_read_only_sql(sql: str) -> bool:
    stripped = sql.strip().lstrip("(").strip()
    return bool(re.match(r"^(SELECT|WITH)\b", stripped, flags=re.IGNORECASE))


def execute_sql(db_path: Path, sql: str, max_steps: int = 250_000) -> dict[str, Any]:
    if not _is_read_only_sql(sql):
        return {"rows": None, "columns": [], "error": "Only SELECT/WITH SQL is allowed in eval", "latency": 0.0}

    start = time.perf_counter()
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.create_function("TO_NUMBER", 1, parse_number)
        steps = {"count": 0}

        def progress_handler() -> int:
            steps["count"] += 1
            return 1 if steps["count"] > max_steps else 0

        conn.set_progress_handler(progress_handler, 100)
        cursor = conn.execute(sql)
        rows = cursor.fetchall()
        columns = [item[0] for item in (cursor.description or [])]
        return {"rows": rows, "columns": columns, "error": None, "latency": time.perf_counter() - start}
    except Exception as exc:
        return {"rows": None, "columns": [], "error": str(exc), "latency": time.perf_counter() - start}
    finally:
        if conn is not None:
            conn.close()


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    text = str(value).strip()
    try:
        number = float(text)
        return round(number, 6)
    except ValueError:
        return text.lower()


def _normalize_rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    return [tuple(_normalize_value(value) for value in row) for row in rows]


def _order_sensitive(sql: str) -> bool:
    return bool(re.search(r"\bORDER\s+BY\b", sql or "", flags=re.IGNORECASE))


def compare_results(gold_rows: list[tuple[Any, ...]], predicted_rows: list[tuple[Any, ...]], gold_sql: str) -> bool:
    gold_normalized = _normalize_rows(gold_rows)
    predicted_normalized = _normalize_rows(predicted_rows)
    if _order_sensitive(gold_sql):
        return gold_normalized == predicted_normalized
    return Counter(gold_normalized) == Counter(predicted_normalized)


def normalize_sql_for_exact_match(sql: str) -> str:
    value = (sql or "").strip().rstrip(";").lower()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s*([(),=<>+\-*/])\s*", r"\1", value)
    return value


def exact_match_sql(gold_sql: str, predicted_sql: str) -> bool:
    return normalize_sql_for_exact_match(gold_sql) == normalize_sql_for_exact_match(predicted_sql)


def classify_error(
    question: str,
    gold_sql: str,
    predicted_sql: str,
    gold_exec: dict[str, Any],
    predicted_exec: dict[str, Any],
    execution_match: bool,
) -> str:
    if execution_match:
        return "match"
    if predicted_exec.get("error"):
        return "invalid_sql"

    gold_columns = gold_exec.get("columns") or []
    predicted_columns = predicted_exec.get("columns") or []
    gold_rows = gold_exec.get("rows") or []
    predicted_rows = predicted_exec.get("rows") or []
    gold_key = (gold_sql or "").lower()
    predicted_key = (predicted_sql or "").lower()
    question_key = (question or "").lower()

    if len(gold_columns) != len(predicted_columns):
        return "projection_mismatch"
    if re.search(r"\bcount\s*\(", gold_key) and not re.search(r"\bcount\s*\(", predicted_key):
        return "missing_aggregation"
    if re.search(r"\b(avg|sum|min|max)\s*\(", gold_key) and not re.search(r"\b(avg|sum|min|max)\s*\(", predicted_key):
        return "missing_aggregation"
    if " join " in gold_key and " join " not in predicted_key:
        return "wrong_join"
    if "group by" in gold_key and "group by" not in predicted_key:
        return "missing_group_by"
    if ("order by" in gold_key) != ("order by" in predicted_key) or ("limit" in gold_key) != ("limit" in predicted_key):
        return "order_limit_mismatch"
    if ("how many" in question_key or "number of" in question_key) and not re.search(r"\bcount\s*\(", predicted_key):
        return "missing_aggregation"
    if len(gold_rows) != len(predicted_rows):
        return "row_count_mismatch"
    return "result_mismatch"


def evaluate_case(
    case: dict[str, Any],
    table_metadata: dict[str, dict[str, Any]],
    spider_dir: Path,
    provider: str,
    max_steps: int,
) -> dict[str, Any]:
    db_id = str(case.get("db_id", ""))
    question = str(case.get("question", ""))
    gold_sql = str(case.get("query", "")).strip()
    table_meta = table_metadata.get(db_id)
    if not table_meta:
        return {
            "db_id": db_id,
            "question": question,
            "gold_sql": gold_sql,
            "predicted_sql": "",
            "provider": provider,
            "planner_used": False,
            "rag_used": False,
            "generation_error": f"Missing table metadata for db_id={db_id}",
            "gold_error": None,
            "predicted_error": None,
            "error_type": "missing_metadata",
            "execution_valid": False,
            "execution_match": False,
            "exact_match": False,
            "latency_ms": 0,
            "gold_row_count": 0,
            "predicted_row_count": 0,
        }

    schema = _schema_from_table_meta(table_meta)
    semantic_schema = infer_semantic_schema(schema)

    generation_start = time.perf_counter()
    try:
        (
            predicted_sql,
            prompt,
            used_provider,
            rag_used,
            retrieved_examples,
            intent,
            semantic_schema,
            planner_used,
            validation_warnings,
            rag_debug,
        ) = generate_sql(
            question,
            schema,
            provider=provider,
            language="en",
            semantic_schema=semantic_schema,
            task_mode="spider",
            current_db_id=db_id,
        )
        generation_error = None
    except Exception as exc:
        predicted_sql = ""
        prompt = ""
        used_provider = provider
        rag_used = False
        retrieved_examples = []
        intent = {"name": "generation_error"}
        planner_used = False
        validation_warnings = []
        rag_debug = {}
        generation_error = str(exc)
    generation_latency = time.perf_counter() - generation_start

    try:
        db_path = sqlite_path_for_case(spider_dir, db_id)
    except Exception as exc:
        return {
            "db_id": db_id,
            "question": question,
            "gold_sql": gold_sql,
            "predicted_sql": predicted_sql,
            "provider": used_provider,
            "planner_used": planner_used,
            "rag_used": rag_used,
            "retrieved_examples": retrieved_examples,
            "intent": intent,
            "validation_warnings": validation_warnings,
            "rag_debug": rag_debug,
            "generation_error": generation_error,
            "gold_error": None,
            "predicted_error": str(exc),
            "error_type": "missing_database",
            "execution_valid": False,
            "execution_match": False,
            "exact_match": False,
            "latency_ms": round(generation_latency * 1000, 2),
            "gold_row_count": 0,
            "predicted_row_count": 0,
        }

    gold_exec = execute_sql(db_path, gold_sql, max_steps=max_steps)
    if generation_error:
        predicted_exec = {"rows": None, "columns": [], "error": generation_error, "latency": 0.0}
    else:
        predicted_exec = execute_sql(db_path, predicted_sql, max_steps=max_steps)

    gold_rows = gold_exec.get("rows")
    predicted_rows = predicted_exec.get("rows")
    gold_error = gold_exec.get("error")
    predicted_error = predicted_exec.get("error")
    execution_valid = predicted_error is None and predicted_rows is not None
    execution_match = (
        execution_valid
        and gold_error is None
        and gold_rows is not None
        and predicted_rows is not None
        and compare_results(gold_rows, predicted_rows, gold_sql)
    )
    exact_match = exact_match_sql(gold_sql, predicted_sql)
    error_type = classify_error(question, gold_sql, predicted_sql, gold_exec, predicted_exec, execution_match)

    return {
        "db_id": db_id,
        "question": question,
        "gold_sql": gold_sql,
        "predicted_sql": predicted_sql,
        "provider": used_provider,
        "planner_used": planner_used,
        "rag_used": rag_used,
        "retrieved_examples": retrieved_examples,
        "intent": intent,
        "validation_warnings": validation_warnings,
        "rag_debug": rag_debug,
        "generation_error": generation_error,
        "gold_error": gold_error,
        "predicted_error": predicted_error,
        "error_type": error_type,
        "execution_valid": execution_valid,
        "execution_match": execution_match,
        "exact_match": exact_match,
        "latency_ms": round((generation_latency + float(predicted_exec.get("latency", 0.0))) * 1000, 2),
        "gold_latency_ms": round(float(gold_exec.get("latency", 0.0)) * 1000, 2),
        "gold_columns": gold_exec.get("columns", []),
        "predicted_columns": predicted_exec.get("columns", []),
        "gold_row_count": 0 if gold_rows is None else len(gold_rows),
        "predicted_row_count": 0 if predicted_rows is None else len(predicted_rows),
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    if total == 0:
        return {
            "case_count": 0,
            "execution_valid_rate": 0,
            "execution_match_rate": 0,
            "exact_match_rate": 0,
            "planner_used_rate": 0,
            "rag_used_rate": 0,
            "avg_latency_ms": 0,
            "provider_counts": {},
        }

    provider_counts = Counter(str(result.get("provider", "")) for result in results)
    error_counts = Counter(str(result.get("error_type", "unknown")) for result in results if not result.get("execution_match"))
    return {
        "case_count": total,
        "execution_valid_rate": round(sum(1 for result in results if result.get("execution_valid")) / total, 4),
        "execution_match_rate": round(sum(1 for result in results if result.get("execution_match")) / total, 4),
        "exact_match_rate": round(sum(1 for result in results if result.get("exact_match")) / total, 4),
        "planner_used_rate": round(sum(1 for result in results if result.get("planner_used")) / total, 4),
        "rag_used_rate": round(sum(1 for result in results if result.get("rag_used")) / total, 4),
        "avg_latency_ms": round(sum(float(result.get("latency_ms", 0)) for result in results) / total, 2),
        "provider_counts": dict(provider_counts),
        "error_counts": dict(error_counts),
    }


def write_reports(
    results: list[dict[str, Any]],
    summary: dict[str, Any],
    output_dir: Path,
    split: str,
    provider: str,
    spider_dir: Path,
    limit: int | None,
    offset: int,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "split": split,
        "provider": provider,
        "rag_mode": os.getenv("RAG_RETRIEVAL_MODE", "off") if os.getenv("RAG_ENABLED", "false").lower() == "true" else "off",
        "candidate_pool": os.getenv("RAG_CANDIDATE_POOL"),
        "dail_two_pass": os.getenv("DAIL_TWO_PASS"),
        "rag_cross_domain": os.getenv("RAG_CROSS_DOMAIN"),
        "spider_dir": str(spider_dir),
        "limit": limit,
        "offset": offset,
        "summary": summary,
        "results": results,
    }

    json_path = output_dir / "latest.json"
    md_path = output_dir / "latest.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    failed = [result for result in results if not result.get("execution_match")]
    lines = [
        "# Spider Evaluation",
        "",
        f"- Created: {payload['created_at']}",
        f"- Split: `{split}`",
        f"- Provider: `{provider}`",
        f"- RAG mode: `{payload['rag_mode']}`",
        f"- DAIL two-pass: `{payload['dail_two_pass']}`",
        f"- Cases: `{summary['case_count']}`",
        f"- Execution valid: **{summary['execution_valid_rate']:.2%}**",
        f"- Execution match: **{summary['execution_match_rate']:.2%}**",
        f"- Exact match: **{summary['exact_match_rate']:.2%}**",
        f"- Planner used: `{summary['planner_used_rate']:.2%}`",
        f"- RAG used: `{summary['rag_used_rate']:.2%}`",
        f"- Avg latency: `{summary['avg_latency_ms']} ms`",
        f"- Error types: `{summary.get('error_counts', {})}`",
        "",
        "## Failed / Mismatched Cases",
        "",
    ]
    for idx, result in enumerate(failed[:20], start=1):
        lines += [
            f"### {idx}. `{result['db_id']}`",
            f"- Question: {result['question']}",
            f"- Provider: `{result.get('provider')}`, Planner: `{result.get('planner_used')}`, RAG: `{result.get('rag_used')}`",
            f"- Valid: `{result.get('execution_valid')}`, Match: `{result.get('execution_match')}`",
            f"- Error type: `{result.get('error_type')}`",
            f"- Error: `{result.get('predicted_error')}`",
            f"- Gold columns: `{', '.join(map(str, result.get('gold_columns', [])))}`",
            f"- Predicted columns: `{', '.join(map(str, result.get('predicted_columns', [])))}`",
            "",
            "Gold SQL:",
            "```sql",
            result.get("gold_sql", ""),
            "```",
            "Predicted SQL:",
            "```sql",
            result.get("predicted_sql", ""),
            "```",
            "",
        ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate DataQuery Web NL2SQL on Spider by execution result matching.")
    parser.add_argument("--spider-dir", type=Path, default=None, help="Path to spider_data. Defaults to SPIDER_DATA_DIR or local discovery.")
    parser.add_argument("--split", choices=["dev", "test"], default="dev", help="Spider split to evaluate.")
    parser.add_argument("--provider", choices=["auto", "deepseek", "ollama", "mock"], default="mock", help="Provider passed to generate_sql.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum cases to run. Use 0 for all cases.")
    parser.add_argument("--offset", type=int, default=0, help="Start offset in the split file.")
    parser.add_argument("--db-id", default=None, help="Optional db_id filter.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIR, help="Report directory.")
    parser.add_argument("--max-steps", type=int, default=250_000, help="SQLite progress limit per query.")
    parser.add_argument("--rag-mode", choices=["off", "on", "bm25", "embedding", "dail"], default="off", help="RAG mode for Spider eval.")
    parser.add_argument("--candidate-pool", type=int, default=3000, help="Candidate pool size used by RAG retrieval.")
    parser.add_argument("--dail-two-pass", action="store_true", help="Use a draft SQL pass for DAIL skeleton retrieval.")
    parser.add_argument("--dail-threshold", type=float, default=0.85, help="Skeleton similarity threshold for DAIL retrieval.")
    parser.add_argument("--cross-domain", action="store_true", help="Exclude train examples from the same db_id when retrieving.")
    parser.add_argument("--no-rag", action="store_true", help="Compatibility flag. Same as --rag-mode off.")
    parser.add_argument("--use-planner", action="store_true", help="Enable the local Excel/CSV intent planner. Defaults off for Spider.")
    parser.add_argument("--no-planner", action="store_true", help="Compatibility flag. Planner is already off unless --use-planner is set.")
    args = parser.parse_args()

    if args.no_rag or args.rag_mode == "off":
        os.environ["RAG_ENABLED"] = "false"
    else:
        os.environ["RAG_ENABLED"] = "true"
        os.environ["RAG_RETRIEVAL_MODE"] = "embedding" if args.rag_mode == "on" else args.rag_mode
    os.environ["RAG_CANDIDATE_POOL"] = str(args.candidate_pool)
    os.environ["DAIL_TWO_PASS"] = "true" if args.dail_two_pass or args.rag_mode == "dail" else "false"
    os.environ["DAIL_SKELETON_THRESHOLD"] = str(args.dail_threshold)
    os.environ["RAG_CROSS_DOMAIN"] = "true" if args.cross_domain else "false"
    if args.no_planner or not args.use_planner:
        os.environ["PLANNER_ENABLED"] = "false"
    else:
        os.environ["PLANNER_ENABLED"] = "true"
    if args.provider == "mock":
        print("Note: provider=mock validates the eval harness only; use auto/ollama/deepseek to measure real NL2SQL quality.")

    spider_dir = (args.spider_dir or spider_data_dir()).resolve()
    table_metadata = load_table_metadata(spider_dir)
    cases = load_spider_cases(spider_dir, args.split)
    if args.db_id:
        cases = [case for case in cases if case.get("db_id") == args.db_id]
    if args.offset:
        cases = cases[args.offset:]
    limit = None if args.limit == 0 else max(0, args.limit)
    if limit is not None:
        cases = cases[:limit]

    results = [
        evaluate_case(case, table_metadata, spider_dir, args.provider, args.max_steps)
        for case in cases
    ]
    summary = summarize(results)
    json_path, md_path = write_reports(
        results,
        summary,
        args.output_dir,
        args.split,
        args.provider,
        spider_dir,
        limit,
        args.offset,
    )

    print(f"Evaluated {summary['case_count']} Spider {args.split} cases")
    print(f"Execution valid: {summary['execution_valid_rate']:.2%}")
    print(f"Execution match: {summary['execution_match_rate']:.2%}")
    print(f"Exact match: {summary['exact_match_rate']:.2%}")
    print(f"Planner used: {summary['planner_used_rate']:.2%}")
    print(f"RAG used: {summary['rag_used_rate']:.2%}")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
