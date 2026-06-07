import argparse
import importlib.util
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parents[0]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_EVAL_SPEC = importlib.util.spec_from_file_location("dataquery_scripts_evaluate_spider", SCRIPT_DIR / "evaluate_spider.py")
if _EVAL_SPEC is None or _EVAL_SPEC.loader is None:
    raise RuntimeError("Cannot load scripts/evaluate_spider.py")
_EVAL_MODULE = importlib.util.module_from_spec(_EVAL_SPEC)
_EVAL_SPEC.loader.exec_module(_EVAL_MODULE)

DEFAULT_REPORT_DIR = _EVAL_MODULE.DEFAULT_REPORT_DIR
evaluate_case = _EVAL_MODULE.evaluate_case
load_spider_cases = _EVAL_MODULE.load_spider_cases
load_table_metadata = _EVAL_MODULE.load_table_metadata
from modules.rag import spider_data_dir  # noqa: E402


VALID_MODES = {"no_rag", "bm25", "embedding", "dail"}


def _mode_list(raw: str) -> list[str]:
    modes = [item.strip().lower() for item in raw.split(",") if item.strip()]
    invalid = [mode for mode in modes if mode not in VALID_MODES]
    if invalid:
        raise ValueError(f"Unsupported modes: {', '.join(invalid)}")
    return modes or ["no_rag", "bm25", "embedding", "dail"]


def _configure_mode(mode: str, candidate_pool: int, cross_domain: bool, dail_two_pass: bool, dail_threshold: float) -> None:
    os.environ["PLANNER_ENABLED"] = "false"
    os.environ["RAG_CANDIDATE_POOL"] = str(candidate_pool)
    os.environ["RAG_CROSS_DOMAIN"] = "true" if cross_domain else "false"
    os.environ["DAIL_SKELETON_THRESHOLD"] = str(dail_threshold)
    if mode == "no_rag":
        os.environ["RAG_ENABLED"] = "false"
        os.environ["DAIL_TWO_PASS"] = "false"
        os.environ.pop("RAG_RETRIEVAL_MODE", None)
        return

    os.environ["RAG_ENABLED"] = "true"
    os.environ["RAG_RETRIEVAL_MODE"] = mode
    os.environ["DAIL_TWO_PASS"] = "true" if mode == "dail" and dail_two_pass else "false"


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    if total == 0:
        return {
            "case_count": 0,
            "execution_valid_rate": 0,
            "execution_match_rate": 0,
            "exact_match_rate": 0,
            "rag_used_rate": 0,
            "avg_latency_ms": 0,
            "provider_counts": {},
            "error_counts": {},
        }
    return {
        "case_count": total,
        "execution_valid_rate": round(sum(1 for result in results if result.get("execution_valid")) / total, 4),
        "execution_match_rate": round(sum(1 for result in results if result.get("execution_match")) / total, 4),
        "exact_match_rate": round(sum(1 for result in results if result.get("exact_match")) / total, 4),
        "rag_used_rate": round(sum(1 for result in results if result.get("rag_used")) / total, 4),
        "avg_latency_ms": round(sum(float(result.get("latency_ms", 0)) for result in results) / total, 2),
        "provider_counts": dict(Counter(str(result.get("provider", "")) for result in results)),
        "error_counts": dict(Counter(str(result.get("error_type", "unknown")) for result in results if not result.get("execution_match"))),
    }


def _write_report(payload: dict[str, Any], output: Path) -> tuple[Path, Path]:
    output.parent.mkdir(parents=True, exist_ok=True)
    json_path = output if output.suffix.lower() == ".json" else output.with_suffix(".json")
    md_path = json_path.with_suffix(".md")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Spider Benchmark Matrix",
        "",
        f"- Created: `{payload['created_at']}`",
        f"- Split: `{payload['split']}`",
        f"- Provider: `{payload['provider']}`",
        f"- Limit: `{payload['limit']}`",
        f"- Offset: `{payload['offset']}`",
        f"- Candidate pool: `{payload['candidate_pool']}`",
        f"- DAIL threshold: `{payload['dail_threshold']}`",
        f"- DAIL two-pass: `{payload['dail_two_pass']}`",
        "",
        "| Mode | Cases | Valid | EX | EM | RAG | Avg latency | Errors |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for mode, run in payload["runs"].items():
        summary = run["summary"]
        lines.append(
            "| {mode} | {cases} | {valid:.2%} | {ex:.2%} | {em:.2%} | {rag:.2%} | {latency} ms | `{errors}` |".format(
                mode=mode,
                cases=summary["case_count"],
                valid=summary["execution_valid_rate"],
                ex=summary["execution_match_rate"],
                em=summary["exact_match_rate"],
                rag=summary["rag_used_rate"],
                latency=summary["avg_latency_ms"],
                errors=summary.get("error_counts", {}),
            )
        )

    lines += ["", "## Failed Samples", ""]
    for mode, run in payload["runs"].items():
        failed = [result for result in run["results"] if not result.get("execution_match")]
        lines.append(f"### {mode}")
        if not failed:
            lines.append("")
            lines.append("No failed cases.")
            lines.append("")
            continue
        for idx, result in enumerate(failed[:10], start=1):
            lines += [
                f"{idx}. `{result.get('db_id')}` - `{result.get('error_type')}`",
                f"   Question: {result.get('question')}",
                f"   Gold: `{result.get('gold_sql')}`",
                f"   Predicted: `{result.get('predicted_sql')}`",
                "",
            ]

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Spider eval across no-RAG/BM25/embedding/DAIL modes on the same cases.")
    parser.add_argument("--spider-dir", type=Path, default=None, help="Path to spider_data.")
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument("--provider", choices=["auto", "deepseek", "ollama", "mock"], default="deepseek")
    parser.add_argument("--limit", type=int, default=50, help="Maximum cases to run. Use 0 for all cases.")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--db-id", default=None)
    parser.add_argument("--modes", default="no_rag,bm25,embedding,dail")
    parser.add_argument("--candidate-pool", type=int, default=3000)
    parser.add_argument("--dail-threshold", type=float, default=0.85)
    parser.add_argument("--max-steps", type=int, default=250_000)
    parser.add_argument("--cross-domain", action="store_true")
    parser.add_argument("--no-dail-two-pass", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT_DIR / "comparison_latest.json")
    args = parser.parse_args()

    modes = _mode_list(args.modes)
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

    payload: dict[str, Any] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "split": args.split,
        "provider": args.provider,
        "spider_dir": str(spider_dir),
        "limit": limit,
        "offset": args.offset,
        "db_id": args.db_id,
        "candidate_pool": args.candidate_pool,
        "dail_threshold": args.dail_threshold,
        "cross_domain": args.cross_domain,
        "dail_two_pass": not args.no_dail_two_pass,
        "runs": {},
    }

    for mode in modes:
        print(f"\n=== Running Spider mode: {mode} ===")
        _configure_mode(mode, args.candidate_pool, args.cross_domain, not args.no_dail_two_pass, args.dail_threshold)
        start = time.perf_counter()
        results = [
            evaluate_case(case, table_metadata, spider_dir, args.provider, args.max_steps)
            for case in cases
        ]
        summary = _summarize(results)
        summary["wall_time_s"] = round(time.perf_counter() - start, 2)
        payload["runs"][mode] = {"summary": summary, "results": results}
        print(f"Cases: {summary['case_count']}")
        print(f"Execution valid: {summary['execution_valid_rate']:.2%}")
        print(f"Execution match: {summary['execution_match_rate']:.2%}")
        print(f"Exact match: {summary['exact_match_rate']:.2%}")
        print(f"RAG used: {summary['rag_used_rate']:.2%}")

    json_path, md_path = _write_report(payload, args.output)
    print(f"\nJSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
