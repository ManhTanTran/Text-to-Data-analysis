import argparse
import csv
import json
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
import unicodedata
import re

import pandas as pd


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from modules.excel_parser import parse_file_to_sqlite
from modules.labels import humanize_dataframe_columns, normalize_language
from modules.nl2sql import generate_sql
from modules.numeric import parse_number
from modules.semantic_schema import infer_semantic_schema
from modules.viz import df_to_chart_base64


DEFAULT_CASES = BACKEND_DIR / "eval" / "sales_eval_cases.json"
FIXTURE_CASES = {
    "sales": DEFAULT_CASES,
    "finance": BACKEND_DIR / "eval" / "finance_eval_cases.json",
    "student": BACKEND_DIR / "eval" / "student_eval_cases.json",
}
DEFAULT_REPORT_DIR = BACKEND_DIR / "eval_reports"

RUBRIC = {
    "execution": 30,
    "result": 30,
    "value": 20,
    "chart": 10,
    "stability": 10,
}

BAD_SQL_PATTERNS = [
    r'^\s*SELECT\s+"Trung bình doanh thu"\s+FROM',
    r'^\s*SELECT\s+"Trung bình giá trị"\s+FROM',
    r'^\s*SELECT\s+"Tổng doanh thu"\s+FROM',
    r'^\s*SELECT\s+"Tổng giá trị"\s+FROM',
    r'^\s*SELECT\s+"Số lượng nhóm"\s*,',
]


def text_key(value: object) -> str:
    text = str(value or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _csv_bytes(rows: list[list[Any]]) -> bytes:
    with tempfile.NamedTemporaryFile("w", newline="", encoding="utf-8-sig", suffix=".csv", delete=False) as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)
        path = Path(handle.name)
    try:
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


def make_sales_fixture_csv() -> bytes:
    rows = [
        ["Postcode", "Sales_Rep_ID", "Sales_Rep_Name", "Year", "Value"],
        ["1000", "A", "John", "2011", "120000"],
        ["1000", "A", "John", "2012", "180000"],
        ["1000", "A", "John", "2013", "210000"],
        ["2000", "B", "Jane", "2011", "300000"],
        ["2000", "B", "Jane", "2012", "280000"],
        ["2000", "B", "Jane", "2013", "350000"],
        ["3000", "C", "Ashish", "2011", "90000"],
        ["3000", "C", "Ashish", "2012", "110000"],
        ["3000", "C", "Ashish", "2013", "130000"],
        ["4000", "D", "Maria", "2011", "50000"],
        ["4000", "D", "Maria", "2012", "70000"],
        ["5000", "E", "Linh", "2013", "400000"],
    ]
    return _csv_bytes(rows)


def make_finance_fixture_csv() -> bytes:
    rows = [
        ["Date", "Revenue", "COGS"],
        ["2023-01-15", "100000", "42000"],
        ["2023-01-31", "125000", "52000"],
        ["2023-02-15", "118000", "50000"],
        ["2023-02-28", "140000", "61000"],
        ["2023-03-15", "160000", "73000"],
        ["2023-03-31", "155000", "69000"],
    ]
    return _csv_bytes(rows)


def make_student_fixture_csv() -> bytes:
    rows = [
        ["Sheet", "TT", "Mã SV", "Họ tên", "Ngày sinh", "Số TC ĐK", "TBCHK", "ĐRL", "Đề xuất HB", "Ghi chú"],
        ["CNTT", "1", "SV001", "Nguyễn An", "2002-01-10", "18", "9.1", "Tốt", "Xuất sắc", ""],
        ["CNTT", "2", "SV002", "Trần Bình", "2002-02-12", "18", "8.2", "Khá", "Giỏi", ""],
        ["Kinh tế", "1", "SV003", "Lê Chi", "2001-03-20", "20", "8.9", "Tốt", "Xuất sắc", ""],
        ["Kinh tế", "2", "SV004", "Phạm Dũng", "2001-04-22", "20", "7.1", "Khá", "Khá", ""],
        ["Ngôn ngữ", "1", "SV005", "Hoàng Hà", "2002-05-18", "16", "6.4", "Trung bình", "Không đạt", ""],
    ]
    return _csv_bytes(rows)


def load_dataset(data_path: Path | None, fixture: str = "sales") -> tuple[str, dict[str, list[str]]]:
    if data_path:
        raw = data_path.read_bytes()
        filename = data_path.name
    else:
        fixture_builders = {
            "sales": make_sales_fixture_csv,
            "finance": make_finance_fixture_csv,
            "student": make_student_fixture_csv,
        }
        raw = fixture_builders[fixture]()
        filename = f"{fixture}_eval_fixture.csv"
    return parse_file_to_sqlite(raw, filename)


def load_cases(path: Path, limit: int | None) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    return cases[:limit] if limit else cases


def run_sql(db_path: str, sql: str, language: str) -> tuple[pd.DataFrame | None, str | None]:
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.create_function("TO_NUMBER", 1, parse_number)
        df = pd.read_sql_query(sql, conn)
        return humanize_dataframe_columns(df, language=language), None
    except Exception as exc:
        return None, str(exc)
    finally:
        if conn is not None:
            conn.close()


def column_group_present(columns: list[str], group: list[str]) -> bool:
    normalized_columns = [text_key(column) for column in columns]
    for expected in group:
        expected_key = text_key(expected)
        if any(expected_key and expected_key in column for column in normalized_columns):
            return True
    return False


def find_column(columns: list[str], group: list[str]) -> str | None:
    for column in columns:
        column_key = text_key(column)
        if any(text_key(expected) and text_key(expected) in column_key for expected in group):
            return column
    return None


def sequence_sorted(values: list[Any], desc: bool) -> bool:
    parsed = pd.to_numeric(pd.Series(values), errors="coerce").dropna().tolist()
    if len(parsed) < 2:
        return True
    pairs = zip(parsed, parsed[1:])
    return all(left >= right for left, right in pairs) if desc else all(left <= right for left, right in pairs)


def contains_all(sql: str, parts: list[str]) -> bool:
    sql_key = sql.lower()
    return all(part.lower() in sql_key for part in parts)


def contains_none(sql: str, parts: list[str]) -> bool:
    sql_key = sql.lower()
    return all(part.lower() not in sql_key for part in parts)


def has_bad_sql_pattern(sql: str) -> bool:
    return any(re.search(pattern, sql, flags=re.IGNORECASE) for pattern in BAD_SQL_PATTERNS)


def score_case(case: dict[str, Any], result: dict[str, Any], requested_provider: str) -> dict[str, Any]:
    expected = case.get("expected", {})
    df = result.get("df")
    rows = [] if df is None else df.to_dict("records")
    columns = [] if df is None else list(df.columns)
    sql = result.get("sql", "")
    sql_error = result.get("sql_error")
    chart_base64 = result.get("chart_base64")

    execution_ok = sql_error is None

    required_groups = expected.get("required_column_groups", [])
    if required_groups and columns:
        result_score = sum(1 for group in required_groups if column_group_present(columns, group)) / len(required_groups)
    else:
        result_score = 1.0 if execution_ok else 0.0

    if "min_rows" in expected:
        result_score *= 1.0 if len(rows) >= int(expected["min_rows"]) else 0.0
    if "max_rows" in expected:
        result_score *= 1.0 if len(rows) <= int(expected["max_rows"]) else 0.0

    value_checks: list[bool] = []
    if expected.get("sql_must_contain"):
        value_checks.append(contains_all(sql, expected["sql_must_contain"]))
    if expected.get("sql_must_not_contain"):
        value_checks.append(contains_none(sql, expected["sql_must_not_contain"]))
    if expected.get("sort_column_group") and rows:
        sort_column = find_column(columns, expected["sort_column_group"])
        value_checks.append(bool(sort_column) and sequence_sorted([row.get(sort_column) for row in rows], bool(expected.get("sort_desc", True))))
    if "max_rows" in expected:
        value_checks.append(len(rows) <= int(expected["max_rows"]))
    value_score = sum(value_checks) / len(value_checks) if value_checks else (1.0 if execution_ok else 0.0)

    if "expect_chart" in expected:
        chart_ok = bool(chart_base64) is bool(expected["expect_chart"])
    else:
        chart_ok = True

    stability_ok = execution_ok and not has_bad_sql_pattern(sql)
    if requested_provider == "auto":
        stability_ok = stability_ok and result.get("provider") != "mock"

    return {
        "execution": 1.0 if execution_ok else 0.0,
        "result": result_score,
        "value": value_score,
        "chart": 1.0 if chart_ok else 0.0,
        "stability": 1.0 if stability_ok else 0.0,
        "checks": {
            "execution_ok": execution_ok,
            "required_columns": result_score,
            "value_checks": value_checks,
            "chart_ok": chart_ok,
            "stability_ok": stability_ok,
        },
    }


def evaluate_case(case: dict[str, Any], schema: dict[str, list[str]], db_path: str, provider: str) -> dict[str, Any]:
    language = normalize_language(case.get("language"))
    question = case["question"]
    semantic_schema = infer_semantic_schema(schema)

    try:
        (
            sql,
            prompt,
            used_provider,
            rag_used,
            examples,
            intent,
            semantic_schema,
            planner_used,
            validation_warnings,
            rag_debug,
        ) = generate_sql(question, schema, provider=provider, language=language, semantic_schema=semantic_schema)
        generation_error = None
    except Exception as exc:
        sql = ""
        prompt = ""
        used_provider = provider
        rag_used = False
        examples = []
        intent = {"name": "generation_error"}
        planner_used = False
        validation_warnings = []
        rag_debug = {}
        generation_error = str(exc)

    if generation_error:
        df, sql_error = None, generation_error
        chart_base64, chart_error = None, "generation error"
    else:
        df, sql_error = run_sql(db_path, sql, language)
        chart_base64, chart_error = (None, "SQL error") if df is None else df_to_chart_base64(df, question, language=language)

    result = {
        "id": case["id"],
        "question": question,
        "language": language,
        "provider": used_provider,
        "rag_used": rag_used,
        "retrieved_examples": examples,
        "intent": intent,
        "planner_used": planner_used,
        "validation_warnings": validation_warnings,
        "rag_debug": rag_debug,
        "sql": sql,
        "prompt": prompt,
        "sql_error": sql_error,
        "chart_error": chart_error,
        "chart_base64": chart_base64,
        "df": df,
    }
    result["score"] = score_case(case, result, provider)
    return result


def aggregate_scores(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"total": 0, "categories": {}}
    categories = {}
    for name, weight in RUBRIC.items():
        average = sum(result["score"][name] for result in results) / len(results)
        categories[name] = {
            "raw": round(average, 4),
            "points": round(average * weight, 2),
            "weight": weight,
        }
    total = round(sum(item["points"] for item in categories.values()), 2)
    return {"total": total, "categories": categories}


def result_for_json(result: dict[str, Any]) -> dict[str, Any]:
    df = result.get("df")
    return {
        "id": result["id"],
        "question": result["question"],
        "language": result["language"],
        "provider": result["provider"],
        "rag_used": result["rag_used"],
        "intent": result.get("intent"),
        "planner_used": result.get("planner_used"),
        "validation_warnings": result.get("validation_warnings", []),
        "sql": result["sql"],
        "sql_error": result["sql_error"],
        "chart_error": result["chart_error"],
        "chart_used": bool(result.get("chart_base64")),
        "columns": [] if df is None else list(df.columns),
        "row_count": 0 if df is None else len(df),
        "score": result["score"],
        "retrieved_examples": result["retrieved_examples"],
    }


def write_reports(results: list[dict[str, Any]], summary: dict[str, Any], output_dir: Path, provider: str, data_path: Path | None, fixture: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "provider": provider,
        "data": str(data_path) if data_path else f"built-in {fixture} fixture",
        "summary": summary,
        "results": [result_for_json(result) for result in results],
    }

    json_path = output_dir / "latest.json"
    md_path = output_dir / "latest.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# DataQuery Web Evaluation",
        "",
        f"- Created: {payload['created_at']}",
        f"- Provider: `{provider}`",
        f"- Data: `{payload['data']}`",
        f"- Total score: **{summary['total']}/100**",
        "",
        "## Category Scores",
        "",
    ]
    for name, item in summary["categories"].items():
        lines.append(f"- `{name}`: {item['points']}/{item['weight']} ({item['raw']:.2%})")
    lines += ["", "## Cases", ""]
    for result in payload["results"]:
        status = "PASS" if result["score"]["execution"] and result["score"]["result"] >= 0.75 else "CHECK"
        lines.append(f"### {status} {result['id']}")
        lines.append(f"- Question: {result['question']}")
        lines.append(f"- Provider: `{result['provider']}`, RAG: `{result['rag_used']}`")
        lines.append(f"- Rows: `{result['row_count']}`, Chart: `{result['chart_used']}`")
        lines.append(f"- SQL error: `{result['sql_error']}`")
        lines.append(f"- Columns: `{', '.join(result['columns'])}`")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate DataQuery Web text-to-SQL behavior on sales cases.")
    parser.add_argument("--data", type=Path, default=None, help="Optional CSV/XLSX/JSON data file. Defaults to a built-in sales fixture.")
    parser.add_argument("--fixture", default="sales", choices=sorted(FIXTURE_CASES), help="Built-in fixture to use when --data is omitted.")
    parser.add_argument("--cases", type=Path, default=None, help="Eval case JSON path. Defaults to the matching fixture cases.")
    parser.add_argument("--provider", default="auto", choices=["auto", "deepseek", "ollama", "mock"], help="LLM provider to use.")
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N cases.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIR, help="Directory for latest.json/latest.md reports.")
    args = parser.parse_args()

    case_path = args.cases or FIXTURE_CASES[args.fixture]
    db_path, schema = load_dataset(args.data, args.fixture)
    cases = load_cases(case_path, args.limit)
    results = [evaluate_case(case, schema, db_path, args.provider) for case in cases]
    summary = aggregate_scores(results)
    json_path, md_path = write_reports(results, summary, args.output_dir, args.provider, args.data, args.fixture)

    print(f"Evaluated {len(results)} cases")
    print(f"Score: {summary['total']}/100")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
