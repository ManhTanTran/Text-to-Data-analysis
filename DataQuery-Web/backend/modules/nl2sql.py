import os
import json
import re
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

from openai import OpenAI
from modules.intent_planner import plan_sql
from modules.labels import display_column_label, normalize_language
from modules.rag import (
    dail_two_pass,
    format_examples_for_prompt,
    rag_retrieval_mode,
    retrieve_examples,
    sql2skeleton,
)
from modules.relational_schema import format_create_table_schema, format_relational_schema_for_prompt
from modules.semantic_schema import format_semantic_schema_for_prompt, infer_semantic_schema, semantic_suggestions
from modules.sql_validator import validate_sql_shape


def _load_local_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


_load_local_env()

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))

SUPPORTED_PROVIDERS = {"ollama", "deepseek", "auto", "mock"}


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def planner_enabled() -> bool:
    return _env_bool("PLANNER_ENABLED", True)


def _client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url=DEEPSEEK_BASE_URL,
    )


def get_default_provider() -> str:
    provider = os.getenv("LLM_PROVIDER", "auto").strip().lower()
    return provider if provider in SUPPORTED_PROVIDERS else "auto"


def is_ollama_alive() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=2) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _ollama_complete(prompt: str, temperature: float = 0.0, max_tokens: int = 512) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Cannot connect to Ollama at {OLLAMA_BASE_URL}. "
            f"Start it with 'ollama serve' and pull model '{OLLAMA_MODEL}'."
        ) from exc

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid Ollama response: {raw[:200]}") from exc

    text = str(body.get("response", "")).strip()
    if not text:
        raise RuntimeError("Empty response from Ollama")
    return text


def format_schema(schema: dict) -> str:
    """Format schema with double-quoted identifiers so the AI knows to quote them in SQL."""
    lines = ['-- Column and table names must always be wrapped in double quotes in SQL']
    for table, cols in schema.items():
        lines.append(f'\nTable: "{table}"')
        lines += [f'  - "{c}"' for c in cols]
    return "\n".join(lines)


def format_schema_as_create_table(schema: dict, relational_schema: dict | None = None) -> str:
    return format_create_table_schema(schema, relational_schema=relational_schema)


def _schema_to_spider_meta(schema: dict) -> dict:
    table_names = list(schema.keys())
    column_names: list[list] = [[-1, "*"]]
    for table_idx, table in enumerate(table_names):
        for col in schema.get(table, []):
            column_names.append([table_idx, col])
    return {"table_names_original": table_names, "column_names_original": column_names}


def _build_spider_prompt(schema_str: str, question: str, rag_block: str = "", dail_style: bool = False) -> str:
    if dail_style:
        return (
            f"{rag_block + chr(10) + chr(10) if rag_block else ''}"
            "/* Given the following database schema: */\n"
            f"{schema_str}\n\n"
            f"/* Answer the following: {question} */\n"
            "Return the complete SQLite SQL query only, with no explanation."
        )

    return (
        "You are a SQLite SQL expert for the Spider text-to-SQL benchmark.\n\n"
        f"Database schema:\n{schema_str}\n\n"
        f"{rag_block + chr(10) + chr(10) if rag_block else ''}"
        f"Write a SQL query to answer: {question}\n\n"
        "CRITICAL rules:\n"
        "- Return ONLY the SQL, no explanation, no markdown fences\n"
        "- Use SQLite syntax\n"
        "- Use ONLY the exact source table and column names from the schema\n"
        "- NEVER translate, paraphrase, humanize, or localize source table/column identifiers\n"
        "- Prefer plain Spider-style SQL without display aliases; do not add AS aliases unless required by SQL syntax\n"
        "- SELECT only the columns or expressions explicitly requested by the question; do not add helpful extra columns\n"
        "- If the question asks for a number per group, SELECT the group column and COUNT(*)\n"
        "- If the question asks for the item with the highest/lowest count, use GROUP BY, ORDER BY COUNT(*) DESC/ASC, and LIMIT 1\n"
        "- If the question asks for the item with the highest/lowest value, ORDER BY that value and LIMIT 1\n"
        "- Use JOIN only when columns required by the question are in different tables\n"
        "- Do not use TO_NUMBER unless the source column is text-formatted numeric data; Spider numeric columns can be aggregated directly\n"
        "- Do not invent filters, dates, or columns not stated in the question or schema"
    )


def _complete_prompt(prompt: str, selected_provider: str, max_tokens: int = 512) -> tuple[str, str]:
    provider = (selected_provider or get_default_provider()).strip().lower()
    if provider == "auto":
        if os.getenv("DEEPSEEK_API_KEY"):
            try:
                resp = _client().chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=max_tokens,
                )
                return resp.choices[0].message.content, "deepseek"
            except Exception:
                if is_ollama_alive():
                    return _ollama_complete(prompt, temperature=0.0, max_tokens=max_tokens), "ollama"
                raise
        if is_ollama_alive():
            return _ollama_complete(prompt, temperature=0.0, max_tokens=max_tokens), "ollama"
        raise RuntimeError("No LLM provider available for auto mode")

    if provider == "ollama":
        return _ollama_complete(prompt, temperature=0.0, max_tokens=max_tokens), "ollama"

    if provider == "deepseek":
        resp = _client().chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content, "deepseek"

    raise RuntimeError(f"Unsupported completion provider: {selected_provider}")


def _strip_sql_response(raw: str) -> str:
    sql = raw.strip()
    if sql.startswith("```"):
        parts = sql.split("\n")
        sql = "\n".join(parts[1:-1] if parts[-1].strip() == "```" else parts[1:])
    sql = re.sub(r"^\s*sql\s*:\s*", "", sql, flags=re.IGNORECASE)
    return sql.strip()


def _clean_sql(raw: str, schema: dict | None = None, question: str = "", repair_numeric: bool = True) -> str:
    sql = _strip_sql_response(raw)
    sql = _repair_alias_before_aggregate(sql)
    sql = _repair_translated_identifiers(sql, schema or {})
    sql = _repair_sqlite_date_functions(sql)
    sql = _repair_date_alias_references(sql)
    sql = _repair_ungrounded_date_filters(sql, question, schema or {})
    if repair_numeric:
        sql = _repair_numeric_aggregates(sql)
    return sql.strip().rstrip(";") + ";"


def _text_key(value: str) -> str:
    text = str(value or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _find_column(cols: list[str], *candidates: str) -> str | None:
    by_key = {_text_key(col): col for col in cols}
    for candidate in candidates:
        found = by_key.get(_text_key(candidate))
        if found:
            return found
    return None


def _performance_classification_sql(question: str, schema: dict, language: str) -> str | None:
    key = _text_key(question)
    compact_key = key.replace(" ", "")
    wants_classification = any(term in key for term in ["phan loai", "classify", "segment", "bucket"]) or "phnloi" in compact_key
    wants_grouping = any(term in key for term in ["nhom", "group"]) or "nhm" in compact_key
    wants_performance = (
        any(term in key for term in ["hieu suat", "performance", "ban hang", "sales"])
        or any(term in compact_key for term in ["hiusut", "bnhng"])
    )
    if not (wants_performance and (wants_classification or wants_grouping)):
        return None

    for table, cols in schema.items():
        sales_rep_col = _find_column(cols, "Sales_Rep_ID", "Sales Rep ID", "sales representative id", "rep id")
        value_col = _find_column(cols, "Value", "Sales Value", "Revenue", "Amount", "Total Sales")
        if not sales_rep_col or not value_col:
            continue

        if normalize_language(language) == "en":
            rep_label = "Sales Rep ID"
            total_label = "Total Sales Value"
            avg_label = "Average Sales Value"
            count_label = "Transaction Count"
            group_label = "Performance Group"
            high, medium, low = "High", "Medium", "Low"
        else:
            rep_label = "Mã nhân viên bán hàng"
            total_label = "Tổng giá trị bán hàng"
            avg_label = "Trung bình giá trị bán hàng"
            count_label = "Số giao dịch"
            group_label = "Nhóm hiệu suất"
            high, medium, low = "Cao", "Trung bình", "Thấp"

        return (
            'WITH "rep_performance" AS (\n'
            f'  SELECT "{sales_rep_col}" AS "{rep_label}",\n'
            f'         SUM(TO_NUMBER("{value_col}")) AS "{total_label}",\n'
            f'         AVG(TO_NUMBER("{value_col}")) AS "{avg_label}",\n'
            f'         COUNT(*) AS "{count_label}"\n'
            f'  FROM "{table}"\n'
            f'  GROUP BY "{sales_rep_col}"\n'
            '),\n'
            '"ranked" AS (\n'
            f'  SELECT "{rep_label}", "{total_label}", "{avg_label}", "{count_label}",\n'
            f'         NTILE(3) OVER (ORDER BY "{total_label}" DESC) AS "_performance_bucket"\n'
            '  FROM "rep_performance"\n'
            ')\n'
            f'SELECT "{rep_label}", "{total_label}", "{avg_label}", "{count_label}",\n'
            f'       CASE "_performance_bucket" WHEN 1 THEN \'{high}\' WHEN 2 THEN \'{medium}\' ELSE \'{low}\' END AS "{group_label}"\n'
            'FROM "ranked"\n'
            f'ORDER BY "{total_label}" DESC;'
        )

    return None


def _above_average_sales_performance_sql(question: str, schema: dict, language: str) -> str | None:
    key = _text_key(question)
    compact_key = key.replace(" ", "")
    wants_above_average = (
        any(term in key for term in ["vuot troi", "vuot binh quan", "tren binh quan", "so voi binh quan", "above average", "outperform", "outstanding"])
        or any(term in compact_key for term in ["vttroi", "vtbinhquan", "trenbinhquan", "sovoibinhquan", "bnhquan"])
    )
    wants_sales_performance = (
        any(term in key for term in ["hieu suat", "kinh doanh", "ban hang", "sales", "performance"])
        or any(term in compact_key for term in ["hiusuat", "kinhdoanh", "bnhng"])
    )
    if not (wants_above_average and wants_sales_performance):
        return None

    for table, cols in schema.items():
        value_col = _find_column(cols, "Value", "Sales Value", "Revenue", "Amount", "Total Sales")
        if not value_col:
            continue

        sales_rep_id_col = _find_column(cols, "Sales_Rep_ID", "Sales Rep ID", "sales representative id", "rep id")
        sales_rep_name_col = _find_column(cols, "Sales_Rep_Name", "Sales Rep Name", "sales representative name", "rep name")
        if not sales_rep_id_col and not sales_rep_name_col:
            continue

        if normalize_language(language) == "en":
            id_label = "Sales Rep ID"
            name_label = "Sales Rep Name"
            total_label = "Total Sales Value"
            avg_label = "Average Sales Value"
            count_label = "Transaction Count"
            benchmark_label = "Average Total Sales Value"
            diff_label = "Above Average Difference"
            pct_label = "Above Average Rate (%)"
        else:
            id_label = "Mã nhân viên bán hàng"
            name_label = "Tên nhân viên bán hàng"
            total_label = "Tổng giá trị bán hàng"
            avg_label = "Trung bình giá trị bán hàng"
            count_label = "Số giao dịch"
            benchmark_label = "Bình quân tổng giá trị bán hàng"
            diff_label = "Chênh lệch so với bình quân"
            pct_label = "Tỷ lệ vượt bình quân (%)"

        select_dimensions: list[str] = []
        group_by_columns: list[str] = []
        projected_dimensions: list[str] = []

        if sales_rep_id_col:
            select_dimensions.append(f'         "{sales_rep_id_col}" AS "{id_label}"')
            group_by_columns.append(f'"{sales_rep_id_col}"')
            projected_dimensions.append(f'"rep_performance"."{id_label}"')
        if sales_rep_name_col:
            select_dimensions.append(f'         "{sales_rep_name_col}" AS "{name_label}"')
            group_by_columns.append(f'"{sales_rep_name_col}"')
            projected_dimensions.append(f'"rep_performance"."{name_label}"')

        select_dimension_sql = ",\n".join(select_dimensions)
        group_by_sql = ", ".join(group_by_columns)
        projected_dimension_sql = ", ".join(projected_dimensions)

        return (
            'WITH "rep_performance" AS (\n'
            f'  SELECT\n{select_dimension_sql},\n'
            f'         SUM(TO_NUMBER("{value_col}")) AS "{total_label}",\n'
            f'         AVG(TO_NUMBER("{value_col}")) AS "{avg_label}",\n'
            f'         COUNT(*) AS "{count_label}"\n'
            f'  FROM "{table}"\n'
            f'  WHERE TO_NUMBER("{value_col}") IS NOT NULL\n'
            f'  GROUP BY {group_by_sql}\n'
            '),\n'
            '"benchmark" AS (\n'
            f'  SELECT AVG("{total_label}") AS "{benchmark_label}"\n'
            '  FROM "rep_performance"\n'
            ')\n'
            f'SELECT {projected_dimension_sql},\n'
            f'       "rep_performance"."{total_label}",\n'
            f'       "rep_performance"."{avg_label}",\n'
            f'       "rep_performance"."{count_label}",\n'
            f'       "benchmark"."{benchmark_label}",\n'
            f'       "rep_performance"."{total_label}" - "benchmark"."{benchmark_label}" AS "{diff_label}",\n'
            f'       ROUND(("rep_performance"."{total_label}" / NULLIF("benchmark"."{benchmark_label}", 0) - 1) * 100, 2) AS "{pct_label}"\n'
            'FROM "rep_performance"\n'
            'CROSS JOIN "benchmark"\n'
            f'WHERE "rep_performance"."{total_label}" > "benchmark"."{benchmark_label}"\n'
            f'ORDER BY "rep_performance"."{total_label}" DESC;'
        )

    return None


def _top_sales_reps_by_revenue_sql(question: str, schema: dict, language: str) -> str | None:
    key = _text_key(question)
    compact_key = key.replace(" ", "")
    wants_top = (
        "top" in key
        or any(term in key for term in ["cao nhat", "highest", "largest", "most", "max"])
        or "caonhat" in compact_key
    )
    wants_revenue = any(term in key for term in ["doanh thu", "revenue", "sales value", "total sales"]) or "doanhthu" in compact_key
    wants_rep = (
        any(term in key for term in ["nhan vien", "sales rep", "sales representative", "employee"])
        or any(term in compact_key for term in ["nhanvien", "nhnvien", "nhnvin", "salesrep"])
    )
    if not (wants_top and wants_revenue and wants_rep):
        return None

    limit_match = re.search(r"\btop\s+(\d+)\b", key) or re.search(r"\b(\d+)\b", key)
    limit = int(limit_match.group(1)) if limit_match else 10
    limit = max(1, min(limit, 100))

    for table, cols in schema.items():
        value_col = _find_column(cols, "Value", "Sales Value", "Revenue", "Amount", "Total Sales")
        if not value_col:
            continue

        sales_rep_id_col = _find_column(cols, "Sales_Rep_ID", "Sales Rep ID", "sales representative id", "rep id")
        sales_rep_name_col = _find_column(cols, "Sales_Rep_Name", "Sales Rep Name", "sales representative name", "rep name")
        if not sales_rep_id_col and not sales_rep_name_col:
            continue

        if normalize_language(language) == "en":
            id_label = "Sales Rep ID"
            name_label = "Sales Rep Name"
            total_label = "Total Revenue"
            avg_label = "Average Revenue"
            count_label = "Transaction Count"
        else:
            id_label = "Mã nhân viên bán hàng"
            name_label = "Tên nhân viên bán hàng"
            total_label = "Tổng doanh thu"
            avg_label = "Trung bình doanh thu"
            count_label = "Số giao dịch"

        select_dimensions: list[str] = []
        group_by_columns: list[str] = []
        if sales_rep_id_col:
            select_dimensions.append(f'  "{sales_rep_id_col}" AS "{id_label}"')
            group_by_columns.append(f'"{sales_rep_id_col}"')
        if sales_rep_name_col:
            select_dimensions.append(f'  "{sales_rep_name_col}" AS "{name_label}"')
            group_by_columns.append(f'"{sales_rep_name_col}"')

        select_dimension_sql = ",\n".join(select_dimensions)
        group_by_sql = ", ".join(group_by_columns)

        return (
            'SELECT\n'
            f'{select_dimension_sql},\n'
            f'  SUM(TO_NUMBER("{value_col}")) AS "{total_label}",\n'
            f'  AVG(TO_NUMBER("{value_col}")) AS "{avg_label}",\n'
            f'  COUNT(*) AS "{count_label}"\n'
            f'FROM "{table}"\n'
            f'WHERE TO_NUMBER("{value_col}") IS NOT NULL\n'
            f'GROUP BY {group_by_sql}\n'
            f'ORDER BY "{total_label}" DESC\n'
            f'LIMIT {limit};'
        )

    return None


def _sales_rep_count_sql(question: str, schema: dict, language: str) -> str | None:
    key = _text_key(question)
    compact_key = key.replace(" ", "")
    wants_count = (
        any(term in key for term in ["bao nhieu", "so luong", "dem", "count", "how many", "number of", "total number"])
        or any(term in compact_key for term in ["baonhieu", "soluong"])
    )
    wants_rep = (
        any(term in key for term in ["nhan vien", "sales rep", "sales representative", "employee"])
        or any(term in compact_key for term in ["nhanvien", "nhnvien", "nhnvin", "salesrep"])
    )
    wants_transaction_count = any(
        term in key
        for term in ["giao dich", "don hang", "ban ghi", "dong", "transaction", "order", "record", "row"]
    )
    if not (wants_count and wants_rep) or wants_transaction_count:
        return None

    for table, cols in schema.items():
        sales_rep_id_col = _find_column(cols, "Sales_Rep_ID", "Sales Rep ID", "sales representative id", "rep id")
        sales_rep_name_col = _find_column(cols, "Sales_Rep_Name", "Sales Rep Name", "sales representative name", "rep name")
        count_col = sales_rep_id_col or sales_rep_name_col
        if not count_col:
            continue

        label = "Employee Count" if normalize_language(language) == "en" else "Số lượng nhân viên"
        return f'SELECT COUNT(DISTINCT "{count_col}") AS "{label}"\nFROM "{table}";'

    return None


def _student_count_sql(question: str, schema: dict, language: str) -> str | None:
    key = _text_key(question)
    compact_key = key.replace(" ", "")
    wants_count = (
        any(term in key for term in ["bao nhieu", "so luong", "dem", "count", "how many", "number of", "total number"])
        or any(term in compact_key for term in ["baonhieu", "soluong"])
    )
    wants_student = (
        any(term in key for term in ["sinh vien", "student"])
        or any(term in compact_key for term in ["sinhvien"])
        or re.search(r"\bsv\b", key) is not None
    )
    if not (wants_count and wants_student):
        return None

    for table, cols in schema.items():
        student_id_col = _find_column(cols, "Mã SV", "Ma SV", "Student ID", "Student Code")
        student_name_col = _find_column(cols, "Họ tên", "Ho ten", "Student Name", "Name")
        count_col = student_id_col or student_name_col
        if not count_col:
            continue

        label = "Student Count" if normalize_language(language) == "en" else "Số lượng sinh viên"
        return f'SELECT COUNT(DISTINCT "{count_col}") AS "{label}"\nFROM "{table}";'

    return None


def _top_students_by_score_sql(question: str, schema: dict, language: str) -> str | None:
    key = _text_key(question)
    compact_key = key.replace(" ", "")
    wants_top = (
        "top" in key
        or any(term in key for term in ["cao nhat", "highest", "largest", "best", "max"])
        or "caonhat" in compact_key
    )
    wants_student = (
        any(term in key for term in ["sinh vien", "student"])
        or any(term in compact_key for term in ["sinhvien"])
    )
    wants_score = any(term in key for term in ["diem", "iem", "tbchk", "gpa", "score", "grade"])
    if not (wants_top and wants_student and wants_score):
        return None

    limit_match = re.search(r"\btop\s+(\d+)\b", key) or re.search(r"\b(\d+)\b", key)
    limit = int(limit_match.group(1)) if limit_match else 10
    limit = max(1, min(limit, 100))

    for table, cols in schema.items():
        score_col = _find_column(cols, "TBCHK", "GPA", "Score", "Grade", "Diem", "Diem trung binh")
        if not score_col:
            continue

        sheet_col = _find_column(cols, "Sheet", "Nganh", "Department", "Major")
        student_id_col = _find_column(cols, "Mã SV", "Ma SV", "Student ID", "Student Code")
        student_name_col = _find_column(cols, "Họ tên", "Ho ten", "Student Name", "Name")
        if not student_id_col and not student_name_col:
            continue

        if normalize_language(language) == "en":
            sheet_label = "Sheet"
            id_label = "Student ID"
            name_label = "Student Name"
            score_label = "Score"
        else:
            sheet_label = "Sheet"
            id_label = "Mã sinh viên"
            name_label = "Họ tên"
            score_label = "TBCHK"

        select_columns: list[str] = []
        if sheet_col:
            select_columns.append(f'  "{sheet_col}" AS "{sheet_label}"')
        if student_id_col:
            select_columns.append(f'  "{student_id_col}" AS "{id_label}"')
        if student_name_col:
            select_columns.append(f'  "{student_name_col}" AS "{name_label}"')
        select_columns.append(f'  TO_NUMBER("{score_col}") AS "{score_label}"')
        select_sql = ",\n".join(select_columns)

        return (
            'SELECT\n'
            f'{select_sql}\n'
            f'FROM "{table}"\n'
            f'WHERE TO_NUMBER("{score_col}") IS NOT NULL\n'
            f'ORDER BY "{score_label}" DESC\n'
            f'LIMIT {limit};'
        )

    return None


def _average_student_score_by_group_sql(question: str, schema: dict, language: str) -> str | None:
    key = _text_key(question)
    compact_key = key.replace(" ", "")
    wants_average = any(term in key for term in ["trung binh", "binh quan", "average", "avg", "mean"])
    wants_score = any(term in key for term in ["diem", "iem", "tbchk", "gpa", "score", "grade"])
    wants_grouped = (
        any(term in key for term in ["theo tung", "theo moi", "for each", "by each", "group by"])
        or "theotung" in compact_key
        or "theomoi" in compact_key
    )
    wants_highest_group = (
        any(term in key for term in ["cao nhat", "highest", "largest", "best", "max"])
        or "caonhat" in compact_key
    )
    if not (wants_average and wants_score and (wants_grouped or wants_highest_group)):
        return None

    for table, cols in schema.items():
        score_col = _find_column(cols, "TBCHK", "GPA", "Score", "Grade", "Diem", "Diem trung binh")
        if not score_col:
            continue

        group_col = None
        for col in cols:
            col_key = _text_key(col)
            if col_key and (col_key in key or col_key.replace(" ", "") in compact_key) and col != score_col:
                group_col = col
                break

        if not group_col and any(term in key for term in ["sheet", "nganh", "major", "department"]):
            group_col = _find_column(cols, "Sheet", "Nganh", "Department", "Major")
        if not group_col:
            continue

        if normalize_language(language) == "en":
            group_label = group_col
            avg_label = f"Average {score_col}"
        else:
            group_label = group_col
            avg_label = f"Trung bình {score_col}"

        select_columns = [f'  "{group_col}" AS "{group_label}"']
        select_columns.append(f'  AVG(TO_NUMBER("{score_col}")) AS "{avg_label}"')
        select_sql = ",\n".join(select_columns)
        limit_sql = "\nLIMIT 1" if wants_highest_group else ""

        return (
            'SELECT\n'
            f'{select_sql}\n'
            f'FROM "{table}"\n'
            f'WHERE TO_NUMBER("{score_col}") IS NOT NULL\n'
            f'GROUP BY "{group_col}"\n'
            f'ORDER BY "{avg_label}" DESC{limit_sql};'
        )

    return None


def _scholarship_level_from_question(question: str) -> str | None:
    key = _text_key(question)
    compact_key = key.replace(" ", "")
    if "xuat sac" in key or "excellent" in key:
        return "Xuất sắc"
    if "khong du tin chi" in key or "khongdutinchi" in compact_key:
        return "Không đủ tín chỉ"
    if "khong dat" in key or "khongdat" in compact_key or "not qualified" in key:
        return "Không đạt"
    if re.search(r"\bgioi\b", key) or "good" in key:
        return "Giỏi"
    if re.search(r"\bkha\b", key):
        return "Khá"
    return None


def _students_by_scholarship_sql(question: str, schema: dict, language: str) -> str | None:
    key = _text_key(question)
    compact_key = key.replace(" ", "")
    wants_scholarship = (
        any(term in key for term in ["hoc bong", "de xuat hb", "de xuat", "scholarship", "proposed"])
        or any(term in compact_key for term in ["hocbong", "dexuat"])
    )
    wants_student = (
        any(term in key for term in ["sinh vien", "student", "ai", "who"])
        or any(term in compact_key for term in ["sinhvien"])
    )
    if not (wants_scholarship and wants_student):
        return None

    level = _scholarship_level_from_question(question)

    for table, cols in schema.items():
        scholarship_col = _find_column(cols, "Đề xuất HB", "De xuat HB", "Scholarship", "Hoc bong")
        if not scholarship_col:
            continue

        sheet_col = _find_column(cols, "Sheet", "Nganh", "Department", "Major")
        student_id_col = _find_column(cols, "Mã SV", "Ma SV", "Student ID", "Student Code")
        student_name_col = _find_column(cols, "Họ tên", "Ho ten", "Student Name", "Name")
        score_col = _find_column(cols, "TBCHK", "GPA", "Score", "Grade", "Diem", "Diem trung binh")
        conduct_col = _find_column(cols, "ĐRL", "DRL", "Conduct", "Training score")
        if not student_id_col and not student_name_col:
            continue

        if normalize_language(language) == "en":
            sheet_label = "Sheet"
            id_label = "Student ID"
            name_label = "Student Name"
            score_label = "Score"
            conduct_label = "Conduct"
            scholarship_label = "Scholarship Proposal"
        else:
            sheet_label = "Sheet"
            id_label = "Mã sinh viên"
            name_label = "Họ tên"
            score_label = "TBCHK"
            conduct_label = "ĐRL"
            scholarship_label = "Đề xuất học bổng"

        select_columns: list[str] = []
        if sheet_col:
            select_columns.append(f'  "{sheet_col}" AS "{sheet_label}"')
        if student_id_col:
            select_columns.append(f'  "{student_id_col}" AS "{id_label}"')
        if student_name_col:
            select_columns.append(f'  "{student_name_col}" AS "{name_label}"')
        if score_col:
            select_columns.append(f'  TO_NUMBER("{score_col}") AS "{score_label}"')
        if conduct_col:
            select_columns.append(f'  "{conduct_col}" AS "{conduct_label}"')
        select_columns.append(f'  "{scholarship_col}" AS "{scholarship_label}"')
        select_sql = ",\n".join(select_columns)

        if level:
            where_sql = f'WHERE "{scholarship_col}" = \'{level}\''
        else:
            where_sql = f'WHERE "{scholarship_col}" IS NOT NULL AND TRIM("{scholarship_col}") <> \'\''

        order_columns: list[str] = []
        if score_col:
            order_columns.append(f'"{score_label}" DESC')
        if sheet_col:
            order_columns.append(f'"{sheet_label}" ASC')
        if student_name_col:
            order_columns.append(f'"{name_label}" ASC')
        order_sql = ", ".join(order_columns) if order_columns else f'"{scholarship_label}" ASC'

        return (
            'SELECT\n'
            f'{select_sql}\n'
            f'FROM "{table}"\n'
            f'{where_sql}\n'
            f'ORDER BY {order_sql};'
        )

    return None


def _time_trend_aggregate_sql(question: str, schema: dict, language: str) -> str | None:
    key = _text_key(question)
    compact_key = key.replace(" ", "")
    wants_trend = (
        any(
            term in key
            for term in [
                "tang giam",
                "tang hay giam",
                "xu huong",
                "bien dong",
                "thay doi",
                "theo thoi gian",
                "trend",
                "over time",
                "change over time",
                "increase",
                "decrease",
            ]
        )
        or any(term in compact_key for term in ["tanggiam", "tanghaygiam", "xuhuong", "theothoigian"])
    )
    if not wants_trend:
        return None

    lookup_candidates = ["revenue", "cogs", "profit", "value", "amount", "sales", "cost", "price", "quantity"]
    mentioned_candidates = [candidate for candidate in lookup_candidates if candidate in key or candidate.replace(" ", "") in compact_key]
    if not mentioned_candidates and not any(term in key for term in ["doanh thu", "chi phi", "gia von", "loi nhuan", "gia tri"]):
        return None

    for table, cols in schema.items():
        date_col = _find_column(cols, "Date", "Time", "Timestamp", "Ngày", "Thời gian")
        if not date_col:
            continue

        lookup = _schema_column_lookup({table: cols})
        measure_cols: list[str] = []
        requested_terms = [
            "revenue",
            "doanh thu",
            "sales",
            "cogs",
            "chi phi san xuat",
            "chi phi",
            "gia von",
            "cost of goods sold",
            "cost",
            "profit",
            "loi nhuan",
            "value",
            "gia tri",
            "amount",
        ]
        for term in requested_terms:
            if _text_key(term) in key or _text_key(term).replace(" ", "") in compact_key:
                col = lookup.get(_text_key(term))
                if col and col != date_col and col not in measure_cols:
                    measure_cols.append(col)

        if not measure_cols:
            for col in cols:
                col_key = _text_key(col)
                if col != date_col and any(term in col_key for term in ["revenue", "cogs", "profit", "value", "amount", "sales", "cost"]):
                    measure_cols.append(col)

        if not measure_cols:
            continue

        time_expr = f"strftime('%Y-%m', \"{date_col}\")"
        time_label = "Time" if normalize_language(language) == "en" else "Thời gian"
        select_columns = [f'  {time_expr} AS "{time_label}"']
        order_expr = f'"{time_label}"'

        for col in measure_cols:
            if normalize_language(language) == "en":
                label = f"Total {display_column_label(col, 'en')}"
            else:
                label = f"Tổng {display_column_label(col, 'vi').lower()}"
            select_columns.append(f'  SUM(TO_NUMBER("{col}")) AS "{label}"')

        select_sql = ",\n".join(select_columns)
        return (
            'SELECT\n'
            f'{select_sql}\n'
            f'FROM "{table}"\n'
            f'WHERE "{date_col}" IS NOT NULL\n'
            f'GROUP BY {time_expr}\n'
            f'ORDER BY {order_expr} ASC;'
        )

    return None


def _finalize_generated_sql(raw: str, question: str, schema: dict, language: str, task_mode: str = "app") -> str:
    if (task_mode or "app").strip().lower() == "spider":
        return _clean_sql(raw, schema, question, repair_numeric=False)

    time_trend_sql = _time_trend_aggregate_sql(question, schema, language)
    if time_trend_sql:
        return time_trend_sql
    student_count_sql = _student_count_sql(question, schema, language)
    if student_count_sql:
        return student_count_sql
    students_by_scholarship_sql = _students_by_scholarship_sql(question, schema, language)
    if students_by_scholarship_sql:
        return students_by_scholarship_sql
    average_student_score_sql = _average_student_score_by_group_sql(question, schema, language)
    if average_student_score_sql:
        return average_student_score_sql
    top_students_sql = _top_students_by_score_sql(question, schema, language)
    if top_students_sql:
        return top_students_sql
    sales_rep_count_sql = _sales_rep_count_sql(question, schema, language)
    if sales_rep_count_sql:
        return sales_rep_count_sql
    top_revenue_sql = _top_sales_reps_by_revenue_sql(question, schema, language)
    if top_revenue_sql:
        return top_revenue_sql
    above_average_sql = _above_average_sales_performance_sql(question, schema, language)
    if above_average_sql:
        return above_average_sql
    classification_sql = _performance_classification_sql(question, schema, language)
    if classification_sql:
        return classification_sql
    return _clean_sql(raw, schema, question, repair_numeric=True)


def _repair_alias_before_aggregate(sql: str) -> str:
    """Repair LLM output like `"Alias" (AVG("col"))` into `AVG("col") AS "Alias"`."""
    alias = r'(?P<alias>"[^"]+"|`[^`]+`|\[[^\]]+\])'
    aggregate = r'(?P<expr>(?:AVG|SUM|COUNT|MIN|MAX)\s*\([^)]*\))'
    pattern = re.compile(rf"{alias}\s*\(\s*{aggregate}\s*\)", flags=re.IGNORECASE)
    return pattern.sub(lambda match: f'{match.group("expr")} AS {match.group("alias")}', sql)


def _repair_numeric_aggregates(sql: str) -> str:
    """Use the app's numeric parser for AVG/SUM over uploaded text-number columns."""
    pattern = re.compile(
        r"\b(?P<func>AVG|SUM)\s*\(\s*(?P<distinct>DISTINCT\s+)?(?P<identifier>\"[^\"]+\"|`[^`]+`|\[[^\]]+\])\s*\)",
        flags=re.IGNORECASE,
    )

    def replace(match: re.Match) -> str:
        func = match.group("func").upper()
        distinct = match.group("distinct") or ""
        identifier = match.group("identifier")
        return f"{func}({distinct}TO_NUMBER({identifier}))"

    return pattern.sub(replace, sql)


def _semantic_aliases_for_column(column: str) -> list[str]:
    key = _text_key(column)
    aliases = {column, column.replace("_", " "), key}

    semantic_aliases = {
        "revenue": [
            "doanh thu",
            "tong doanh thu",
            "revenue",
            "sales",
            "sales revenue",
            "turnover",
        ],
        "cogs": [
            "cogs",
            "cost of goods sold",
            "cost",
            "costs",
            "production cost",
            "manufacturing cost",
            "chi phi san xuat",
            "tong chi phi san xuat",
            "gia von",
            "gia von hang ban",
            "chi phi hang ban",
            "chi phi von",
        ],
        "date": [
            "date",
            "ngay",
            "ngay thang",
            "thoi gian",
            "time",
            "period",
            "khoang thoi gian",
        ],
        "profit": [
            "profit",
            "gross profit",
            "net profit",
            "loi nhuan",
            "tong loi nhuan",
        ],
        "value": [
            "value",
            "amount",
            "sales value",
            "gia tri",
            "gia tri ban hang",
        ],
        "price": [
            "price",
            "unit price",
            "gia",
            "don gia",
        ],
        "quantity": [
            "quantity",
            "qty",
            "so luong",
        ],
    }

    for semantic_key, semantic_values in semantic_aliases.items():
        if semantic_key in key or key in semantic_key:
            aliases.update(semantic_values)

    return sorted(aliases)


def _schema_column_lookup(schema: dict) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for cols in schema.values():
        for col in cols:
            for alias in _semantic_aliases_for_column(str(col)):
                alias_key = _text_key(alias)
                if alias_key:
                    lookup[alias_key] = col
            for lang in ("vi", "en"):
                label_key = _text_key(display_column_label(col, lang))
                if label_key:
                    lookup[label_key] = col
    return lookup


def _split_top_level_select_items(select_body: str) -> list[str]:
    items: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    for idx, char in enumerate(select_body):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'", "`"}:
            quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            items.append(select_body[start:idx].strip())
            start = idx + 1
    tail = select_body[start:].strip()
    if tail:
        items.append(tail)
    return items


def _repair_unknown_select_aggregates(sql: str, schema: dict) -> str:
    """Drop aggregate SELECT items that still reference columns absent from the schema."""
    if not schema:
        return sql

    valid_columns = {_text_key(col) for cols in schema.values() for col in cols}
    match = re.search(r"\bSELECT\b(?P<body>.*?)\bFROM\b", sql, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return sql

    items = _split_top_level_select_items(match.group("body"))
    if len(items) <= 1:
        return sql

    kept_items: list[str] = []
    removed_any = False
    for item in items:
        source_part = re.split(r"\bAS\b", item, maxsplit=1, flags=re.IGNORECASE)[0]
        quoted_sources = re.findall(r'"([^"]+)"|`([^`]+)`|\[([^\]]+)\]', source_part)
        source_names = [next(part for part in parts if part) for parts in quoted_sources]
        unknown_sources = [name for name in source_names if _text_key(name) not in valid_columns]
        is_unknown_plain_column = bool(unknown_sources) and re.fullmatch(
            r'\s*(?:"[^"]+"|`[^`]+`|\[[^\]]+\])\s*',
            source_part,
        )
        is_unknown_aggregate = bool(unknown_sources) and re.search(
            r"\b(AVG|SUM|MIN|MAX|COUNT|TO_NUMBER)\s*\(",
            source_part,
            flags=re.IGNORECASE,
        )
        if is_unknown_plain_column or is_unknown_aggregate:
            removed_any = True
            continue
        kept_items.append(item)

    if not removed_any or not kept_items:
        return sql

    repaired_body = " " + ", ".join(kept_items) + " "
    return sql[:match.start("body")] + repaired_body + sql[match.end("body"):]


def _repair_translated_identifiers(sql: str, schema: dict) -> str:
    """Use real schema column names before AS/GROUP BY when the model translated identifiers."""
    if not schema:
        return sql

    select_alias_keys = {
        _text_key(alias)
        for _, alias in re.findall(r'\bAS\s+(["`])([^"`]+)\1', sql, flags=re.IGNORECASE)
        if _text_key(alias)
    }
    alias_to_source = _schema_column_lookup(schema)
    for cols in schema.values():
        for col in cols:
            for lang in ("vi", "en"):
                alias_to_source[display_column_label(col, lang).lower()] = col
            alias_to_source[str(col).replace("_", " ").lower()] = col
            alias_to_source[_text_key(str(col).replace("_", " "))] = col

    def replace_identifier(match: re.Match) -> str:
        quote, name = match.group(1), match.group(2)
        if _text_key(name) in select_alias_keys:
            return match.group(0)

        replacement = alias_to_source.get(_text_key(name)) or alias_to_source.get(name.lower())
        if not replacement or replacement == name:
            return match.group(0)

        prefix = sql[max(0, match.start() - 8):match.start()].upper()
        if re.search(r"\bAS\s*$", prefix):
            return match.group(0)

        return f'{quote}{replacement}{quote}'

    repaired = re.sub(r'(["`])([^"`]+)\1', replace_identifier, sql)
    return _repair_unknown_select_aggregates(repaired, schema)



def _repair_sqlite_date_functions(sql: str) -> str:
    """SQLite has no YEAR/MONTH/DAY helpers, so rewrite them to strftime."""
    replacements = {
        "YEAR": "%Y",
        "MONTH": "%m",
        "DAY": "%d",
    }

    def replace_date_func(match: re.Match) -> str:
        func = match.group(1).upper()
        expr = match.group(2).strip()
        return f"CAST(strftime('{replacements[func]}', {expr}) AS INTEGER)"

    date_expr = r'("[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_.]*)'
    return re.sub(rf"\b(YEAR|MONTH|DAY)\s*\(\s*({date_expr})\s*\)", replace_date_func, sql, flags=re.IGNORECASE)


def _repair_date_alias_references(sql: str) -> str:
    """Replace GROUP BY/PARTITION BY date aliases with their SQLite expressions."""
    date_aliases: dict[str, str] = {}
    pattern = re.compile(
        r"(CAST\s*\(\s*strftime\('[^']+',\s*(?:\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_.]*)\)\s+AS\s+INTEGER\s*\))\s+AS\s+(\"[^\"]+\")",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(sql):
        date_aliases[match.group(2)] = match.group(1)

    if not date_aliases:
        return sql

    def replace_aliases(text: str) -> str:
        for alias, expr in date_aliases.items():
            text = re.sub(rf"(?<!AS\s){re.escape(alias)}", expr, text, flags=re.IGNORECASE)
        return text

    def replace_group_by(match: re.Match) -> str:
        return f"{match.group(1)}{replace_aliases(match.group(2))}{match.group(3)}"

    def replace_partition_by(match: re.Match) -> str:
        return f"{match.group(1)}{replace_aliases(match.group(2))}{match.group(3)}"

    sql = re.sub(r"(\bGROUP\s+BY\s+)(.*?)(\s+(?:HAVING|ORDER\s+BY|LIMIT)\b|$)", replace_group_by, sql, flags=re.IGNORECASE | re.DOTALL)
    sql = re.sub(r"(\bPARTITION\s+BY\s+)(.*?)(\s+ORDER\s+BY|\))", replace_partition_by, sql, flags=re.IGNORECASE | re.DOTALL)
    return sql


def _question_has_explicit_time_value(question: str) -> bool:
    raw = question or ""
    key = _text_key(raw)
    compact_key = key.replace(" ", "")
    patterns = [
        r"\b(?:19|20)\d{2}\b",
        r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b",
        r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b",
        r"\bq[1-4]\b",
        r"\bquarter\s+[1-4]\b",
        r"\bthang\s+\d{1,2}\b",
        r"\bmonth\s+\d{1,2}\b",
        r"\bquy\s+\d{1,2}\b",
    ]
    if any(re.search(pattern, raw, flags=re.IGNORECASE) or re.search(pattern, key, flags=re.IGNORECASE) for pattern in patterns):
        return True
    return any(term in compact_key for term in ["homnay", "hqua", "homqua", "tuannay", "thangnay", "namnay"])


def _schema_date_columns(schema: dict) -> list[str]:
    date_columns: list[str] = []
    date_terms = {"date", "time", "timestamp", "created", "updated", "ngay", "thoi gian"}
    for cols in schema.values():
        for col in cols:
            key = _text_key(col)
            if any(term in key for term in date_terms):
                date_columns.append(col)
    return date_columns


def _clean_where_body(body: str) -> str:
    body = re.sub(r"^\s*(AND|OR)\s+", "", body.strip(), flags=re.IGNORECASE)
    body = re.sub(r"\s+(AND|OR)\s*$", "", body.strip(), flags=re.IGNORECASE)
    body = re.sub(r"\s+(AND|OR)\s+(AND|OR)\s+", r" \2 ", body, flags=re.IGNORECASE)
    return body.strip()


def _remove_date_predicates_from_where(where_body: str, date_columns: list[str]) -> str:
    body = where_body
    literal = r"(?:'[^']*'|\"[^\"]*\"|\b(?:19|20)\d{2}\b)"
    for col in date_columns:
        quoted_col = rf'(?:\"[^\"]+\"\.)?\"{re.escape(col)}\"'
        date_expr = rf"(?:{quoted_col}|DATE\s*\(\s*{quoted_col}\s*\))"
        strftime_expr = rf"(?:CAST\s*\(\s*)?strftime\s*\(\s*'[^']+'\s*,\s*{quoted_col}\s*\)\s*(?:AS\s+INTEGER\s*\))?"

        patterns = [
            rf"\s*(?:AND|OR)?\s*{date_expr}\s+BETWEEN\s+{literal}\s+AND\s+{literal}",
            rf"\s*(?:AND|OR)?\s*{date_expr}\s*(?:=|>=|>|<=|<)\s*{literal}",
            rf"\s*(?:AND|OR)?\s*{literal}\s*(?:=|>=|>|<=|<)\s*{date_expr}",
            rf"\s*(?:AND|OR)?\s*{strftime_expr}\s*(?:=|>=|>|<=|<)\s*{literal}",
            rf"\s*(?:AND|OR)?\s*{literal}\s*(?:=|>=|>|<=|<)\s*{strftime_expr}",
        ]
        for pattern in patterns:
            body = re.sub(pattern, "", body, flags=re.IGNORECASE)
    return _clean_where_body(body)


def _repair_ungrounded_date_filters(sql: str, question: str, schema: dict) -> str:
    """Remove concrete date filters invented by the model when the question has no concrete time value."""
    if _question_has_explicit_time_value(question):
        return sql

    date_columns = _schema_date_columns(schema)
    if not date_columns:
        return sql

    where_match = re.search(
        r"\bWHERE\b(?P<body>.*?)(?P<tail>\s+\bGROUP\s+BY\b|\s+\bORDER\s+BY\b|\s+\bLIMIT\b|;|$)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not where_match:
        return sql

    body = where_match.group("body")
    repaired_body = _remove_date_predicates_from_where(body, date_columns)
    if repaired_body == body.strip():
        return sql

    if repaired_body:
        replacement = f"WHERE {repaired_body}{where_match.group('tail')}"
    else:
        replacement = where_match.group("tail")
    return sql[:where_match.start()] + replacement + sql[where_match.end():]


def generate_mock_sql(schema: dict) -> str:
    table = next(iter(schema), None)
    return f'SELECT * FROM "{table}" LIMIT 10' if table else "SELECT 1"


def generate_sql(
    question: str,
    schema: dict,
    use_mock: bool = False,
    provider: str | None = None,
    language: str = "vi",
    semantic_schema: dict | None = None,
    relational_schema: dict | None = None,
    task_mode: str = "app",
    current_db_id: str | None = None,
) -> tuple[str, str, str, bool, list[dict], dict, dict, bool, list[str], dict]:
    lang = normalize_language(language)
    mode = (task_mode or "app").strip().lower()
    spider_mode = mode == "spider"
    semantic_schema = semantic_schema or infer_semantic_schema(schema)
    planner_result = (
        plan_sql(question, schema, semantic_schema=semantic_schema, language=lang)
        if planner_enabled() and not spider_mode
        else {
            "sql": None,
            "intent": {"name": "llm_fallback", "confidence": 0.0, "details": {"planner_disabled": True, "task_mode": mode}},
            "constraints": [],
            "warnings": [],
            "planner_used": False,
        }
    )
    schema_str = format_schema(schema)
    selected_provider = (provider or get_default_provider()).strip().lower()
    planner_has_sql = bool(planner_result.get("sql"))
    rag_mode = rag_retrieval_mode()
    dail_style_prompt = spider_mode and rag_mode == "dail"
    spider_schema_str = format_schema_as_create_table(schema, relational_schema=relational_schema) if dail_style_prompt else schema_str
    rag_debug: dict = {
        "rag_mode": rag_mode,
        "dail_two_pass": False,
        "draft_sql": None,
        "draft_provider": None,
        "target_skeleton": None,
        "retrieval_stage": "disabled",
    }
    draft_sql = None
    schema_meta = _schema_to_spider_meta(schema)
    if (
        spider_mode
        and rag_mode == "dail"
        and dail_two_pass()
        and not use_mock
        and selected_provider != "mock"
        and not planner_has_sql
    ):
        draft_prompt = _build_spider_prompt(spider_schema_str, question, dail_style=dail_style_prompt)
        try:
            raw_draft, draft_provider = _complete_prompt(draft_prompt, selected_provider, max_tokens=512)
            draft_sql = _clean_sql(raw_draft, schema, question, repair_numeric=False)
            rag_debug.update(
                {
                    "dail_two_pass": True,
                    "draft_sql": draft_sql,
                    "draft_provider": draft_provider,
                    "target_skeleton": sql2skeleton(draft_sql, schema_meta),
                    "retrieval_stage": "draft_sql_skeleton",
                }
            )
        except Exception as exc:
            rag_debug.update({"dail_two_pass": True, "draft_error": str(exc), "retrieval_stage": "draft_failed"})

    rag_examples = (
        []
        if use_mock or selected_provider == "mock" or planner_has_sql
        else retrieve_examples(
            question,
            schema,
            mode=rag_mode,
            target_sql=draft_sql,
            current_db_id=current_db_id,
        )
    )
    if rag_examples:
        rag_debug["retrieval_stage"] = "dail_skeleton_rerank" if rag_mode == "dail" and draft_sql else rag_mode
    rag_block = format_examples_for_prompt(rag_examples)
    semantic_block = format_semantic_schema_for_prompt(semantic_schema)
    relational_block = format_relational_schema_for_prompt(relational_schema)
    planner_constraints = "\n".join(f"- {constraint}" for constraint in planner_result.get("constraints", []))
    alias_rules = (
        'Use Vietnamese for every display alias. Examples: "Sales Rep Name" AS "Tên nhân viên bán hàng", '
        'AVG("Value") AS "Trung bình giá trị", SUM("Value") AS "Tổng giá trị", COUNT(*) AS "Số lượng"'
        if lang == "vi"
        else 'Use English for every display alias. Examples: "Sales Rep Name" AS "Sales Rep Name", '
        'AVG("Value") AS "Average Value", SUM("Value") AS "Total Value", COUNT(*) AS "Count"'
    )
    if spider_mode:
        prompt = _build_spider_prompt(spider_schema_str, question, rag_block, dail_style=dail_style_prompt)
    else:
        prompt = (
            f"You are a SQLite SQL expert. Column names may contain Vietnamese or spaces.\n\n"
            f"Database schema:\n{schema_str}\n\n"
            f"{semantic_block}\n\n"
            f"{relational_block + chr(10) + chr(10) if relational_block else ''}"
            f"Planner constraints:\n{planner_constraints}\n\n"
            f"{rag_block + chr(10) + chr(10) if rag_block else ''}"
            f"Write a SQL query to answer: {question}\n\n"
            "CRITICAL rules:\n"
            "- Return ONLY the SQL, no explanation, no markdown fences\n"
            '- ALWAYS wrap every table name and column name in double quotes, e.g. SELECT "Tên cột" FROM "Table"\n'
            f"- {alias_rules}\n"
            "- NEVER translate source table/column identifiers before AS; use the exact schema names on the left side, translate only display aliases after AS\n"
            '- If the user mentions a field that is not in the schema, do not invent a source column. Use only existing schema columns or a clear equivalent such as "COGS" for production cost/cost of goods sold; otherwise omit the missing field.\n'
            "- Use the inferred relationships only as hints. Join tables only through listed possible relationships or obvious matching key columns.\n"
            "- If no reliable relationship is inferred, avoid joining unrelated uploaded tables.\n"
            '- NEVER invent concrete dates or years. If the user asks about a time range but does not provide actual dates/years, do not add a Date WHERE filter.\n'
            '- If the user asks whether measures increase/decrease, trend, or change over time, group by time period such as strftime(\'%Y-%m\', "Date") and return one row per period, not a single total row.\n'
            "- Alias grouped/display columns too, not only aggregate columns\n"
            '- NEVER write aliases before expressions. Wrong: "Trung bình giá trị" (AVG("Value")). Correct: AVG("Value") AS "Trung bình giá trị"\n'
            "- If using GROUP BY, include the grouped column(s) in SELECT so charts can use them as the x-axis\n"
            '- If the user asks how many sales representatives/employees exist, use COUNT(DISTINCT "Sales_Rep_ID") or COUNT(DISTINCT "Sales_Rep_Name"), not COUNT(*)\n'
            '- If the user asks how many students exist, use COUNT(DISTINCT "Mã SV") when that column exists, not COUNT(*)\n'
            '- If the user asks which students are proposed for a scholarship level, return student id/name and relevant score/scholarship columns, filter "Đề xuất HB" by that level, and do not use GROUP BY unless aggregating\n'
            '- If the user asks for average score/GPA/TBCHK by Sheet/group, include the group column in SELECT and GROUP BY, and use the real source column "TBCHK" when it exists, not a translated alias like "Điểm TBCHK"\n'
            '- If the user asks for top students by score/GPA/TBCHK, return the student id/name plus the score column, ORDER BY the score DESC, and LIMIT N\n'
            '- If the user asks for top N sales representatives/employees by revenue, return the rep identifier/name plus SUM(TO_NUMBER("Value")) as total revenue, ORDER BY total revenue DESC, and LIMIT N\n'
            '- If the user asks to classify/segment/group sales representatives by sales performance, do NOT count groups. Aggregate by "Sales_Rep_ID", calculate SUM(TO_NUMBER("Value")), and assign performance labels with NTILE(3) or CASE: High/Medium/Low or Cao/Trung bình/Thấp\n'
            '- If the user asks which sales representatives are above average/outperforming, return the rep identifier/name, total sales, average sales, the overall average benchmark, and the difference above that benchmark\n'
            '- For AVG/SUM over value, price, amount, revenue, or sales columns, use TO_NUMBER("Column") so text-formatted numbers still calculate correctly\n'
            "- Use SQLite syntax\n"
            '- SQLite has no YEAR(), MONTH(), or DAY(); use CAST(strftime(\'%Y\', "Date column") AS INTEGER), CAST(strftime(\'%m\', "Date column") AS INTEGER), or CAST(strftime(\'%d\', "Date column") AS INTEGER)\n'
            "- Use aggregations and ORDER BY when useful"
        )

    if planner_has_sql:
        sql = _clean_sql(str(planner_result["sql"]), schema, question)
        warnings = list(planner_result.get("warnings", []))
        warnings += validate_sql_shape(sql, question, schema, semantic_schema, planner_result.get("intent"))
        return (
            sql,
            prompt,
            "planner",
            False,
            [],
            planner_result.get("intent", {"name": "unknown"}),
            semantic_schema,
            True,
            warnings,
            rag_debug,
        )

    if use_mock or selected_provider == "mock":
        sql = (
            _time_trend_aggregate_sql(question, schema, lang)
            or _student_count_sql(question, schema, lang)
            or _students_by_scholarship_sql(question, schema, lang)
            or _average_student_score_by_group_sql(question, schema, lang)
            or _top_students_by_score_sql(question, schema, lang)
            or _sales_rep_count_sql(question, schema, lang)
            or _top_sales_reps_by_revenue_sql(question, schema, lang)
            or _above_average_sales_performance_sql(question, schema, lang)
            or _performance_classification_sql(question, schema, lang)
            or generate_mock_sql(schema)
        )
        warnings = validate_sql_shape(sql, question, schema, semantic_schema, planner_result.get("intent"))
        return sql, prompt, "mock", False, [], planner_result.get("intent", {"name": "mock"}), semantic_schema, False, warnings, rag_debug

    def package_generated_sql(raw_sql: str, provider_name: str):
        sql = _finalize_generated_sql(raw_sql, question, schema, lang, task_mode=mode)
        warnings = list(planner_result.get("warnings", []))
        if raw_sql.strip() != sql.strip():
            warnings.append("SQL was repaired after generation.")
        warnings += validate_sql_shape(sql, question, schema, semantic_schema, planner_result.get("intent"))
        return (
            sql,
            prompt,
            provider_name,
            bool(rag_examples),
            rag_examples,
            planner_result.get("intent", {"name": "llm_fallback"}),
            semantic_schema,
            False,
            warnings,
            rag_debug,
        )

    if selected_provider == "auto":
        if os.getenv("DEEPSEEK_API_KEY"):
            try:
                resp = _client().chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                )
                return package_generated_sql(resp.choices[0].message.content, "deepseek")
            except Exception:
                if is_ollama_alive():
                    return package_generated_sql(_ollama_complete(prompt, temperature=0.0), "ollama")
                raise
        if is_ollama_alive():
            selected_provider = "ollama"
        else:
            selected_provider = "mock"

    if selected_provider == "mock":
        sql = (
            _time_trend_aggregate_sql(question, schema, lang)
            or _student_count_sql(question, schema, lang)
            or _students_by_scholarship_sql(question, schema, lang)
            or _average_student_score_by_group_sql(question, schema, lang)
            or _top_students_by_score_sql(question, schema, lang)
            or _sales_rep_count_sql(question, schema, lang)
            or _top_sales_reps_by_revenue_sql(question, schema, lang)
            or _above_average_sales_performance_sql(question, schema, lang)
            or _performance_classification_sql(question, schema, lang)
            or generate_mock_sql(schema)
        )
        warnings = validate_sql_shape(sql, question, schema, semantic_schema, planner_result.get("intent"))
        return sql, prompt, "mock", False, [], planner_result.get("intent", {"name": "mock"}), semantic_schema, False, warnings, rag_debug

    if selected_provider == "ollama":
        return package_generated_sql(_ollama_complete(prompt, temperature=0.0), "ollama")

    if selected_provider == "deepseek":
        resp = _client().chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return package_generated_sql(resp.choices[0].message.content, "deepseek")

    sql = generate_mock_sql(schema)
    warnings = validate_sql_shape(sql, question, schema, semantic_schema, planner_result.get("intent"))
    return sql, prompt, "mock", False, [], planner_result.get("intent", {"name": "mock"}), semantic_schema, False, warnings, rag_debug


def _generate_deepseek_suggestions(prompt: str) -> str:
    resp = _client().chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    return resp.choices[0].message.content


def _find_column_matching(cols: list[str], *candidates: str) -> str | None:
    exact = _find_column(cols, *candidates)
    if exact:
        return exact

    candidate_keys = [_text_key(candidate) for candidate in candidates if _text_key(candidate)]
    for col in cols:
        key = _text_key(col)
        tokens = set(key.split())
        for candidate_key in candidate_keys:
            candidate_tokens = candidate_key.split()
            if candidate_tokens and all(token in tokens for token in candidate_tokens):
                return col
            if len(candidate_key) >= 4 and candidate_key in key:
                return col
    return None


def _clean_suggestion_line(line: str) -> str:
    cleaned = re.sub(r"^\s*(?:[-*]\s*)?(?:\d+[\).\-\s]+)?", "", line or "").strip()
    return cleaned.strip("\"'` ").strip()


def _dedupe_suggestions(suggestions: list[str], limit: int = 6) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for suggestion in suggestions:
        cleaned = _clean_suggestion_line(suggestion)
        key = _text_key(cleaned)
        if not cleaned or len(cleaned) < 8 or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def _schema_text_key(schema: dict) -> str:
    parts: list[str] = []
    for table, cols in schema.items():
        parts.append(str(table))
        parts.extend(str(col) for col in cols)
    return _text_key(" ".join(parts))


def _suggestion_is_schema_grounded(suggestion: str, schema: dict) -> bool:
    key = _text_key(suggestion)
    schema_key = _schema_text_key(schema)

    absent_concepts = [
        (["khu vuc", "vung", "mien", "region", "area", "territory"], ["region", "area", "territory", "state", "city", "country", "province", "district", "khu vuc", "vung", "mien"]),
        (["nha ban le", "retailer", "reseller"], ["retailer", "reseller", "shop", "store", "seller", "nha ban le"]),
        (["san pham", "product", "category", "danh muc"], ["product", "category", "item", "sku", "san pham", "danh muc"]),
        (["khach hang", "customer", "client"], ["customer", "client", "buyer", "khach hang"]),
        (["don vi", "unit"], ["unit", "don vi"]),
    ]
    for blocked_terms, schema_terms in absent_concepts:
        if any(term in key for term in blocked_terms) and not any(term in schema_key for term in schema_terms):
            return False
    return True


def _schema_specific_suggestions(schema: dict, language: str = "vi") -> list[str]:
    lang = normalize_language(language)
    suggestions: list[str] = []

    for table, cols in list(schema.items())[:2]:
        value_col = _find_column_matching(cols, "Value", "Sales Value", "Revenue", "Amount", "Price", "Total Sales", "Doanh thu", "Gia tri")
        sales_rep_id_col = _find_column_matching(cols, "Sales_Rep_ID", "Sales Rep ID", "Sales Representative ID", "Rep ID")
        sales_rep_name_col = _find_column_matching(cols, "Sales_Rep_Name", "Sales Rep Name", "Sales Representative Name", "Rep Name")
        year_col = _find_column_matching(cols, "Year", "Nam")
        postcode_col = _find_column_matching(cols, "Postcode", "Postal Code", "Zip Code", "Zip")
        rep_col = sales_rep_name_col or sales_rep_id_col
        student_id_col = _find_column_matching(cols, "Mã SV", "Ma SV", "Student ID", "Student Code")
        student_name_col = _find_column_matching(cols, "Họ tên", "Ho ten", "Student Name", "Name")
        score_col = _find_column_matching(cols, "TBCHK", "GPA", "Score", "Grade", "Diem", "Diem trung binh")
        sheet_col = _find_column_matching(cols, "Sheet", "Nganh", "Department", "Major")
        conduct_col = _find_column_matching(cols, "ĐRL", "DRL", "Conduct", "Training score")
        scholarship_col = _find_column_matching(cols, "Đề xuất HB", "De xuat HB", "Scholarship", "Hoc bong")

        if lang == "vi":
            if score_col and (student_id_col or student_name_col):
                suggestions.append("Có bao nhiêu sinh viên trong dữ liệu?")
                suggestions.append(f"Top 10 sinh viên có {score_col} cao nhất là ai?")
            if score_col and sheet_col:
                suggestions.append(f"Điểm {score_col} trung bình theo từng {sheet_col} là bao nhiêu?")
                suggestions.append(f"{sheet_col} nào có điểm {score_col} trung bình cao nhất?")
            if scholarship_col:
                suggestions.append(f"Có bao nhiêu sinh viên theo từng mức {scholarship_col}?")
                suggestions.append("Sinh viên nào được đề xuất học bổng Xuất sắc?")
            if conduct_col:
                suggestions.append(f"Phân bố {conduct_col} theo từng mức là như thế nào?")
            if value_col and rep_col:
                suggestions.append("Hiện top 10 nhân viên có doanh thu cao nhất")
                suggestions.append("Có những nhân viên bán hàng nào có hiệu suất kinh doanh vượt trội so với bình quân?")
                suggestions.append("Trung bình giá trị bán hàng của từng nhân viên là bao nhiêu?")
            if value_col and sales_rep_id_col:
                suggestions.append("Làm thế nào để phân loại Sales_Rep_ID thành các nhóm dựa trên hiệu suất bán hàng?")
            if value_col and year_col:
                suggestions.append("Tổng doanh thu theo từng năm là bao nhiêu?")
            if value_col and postcode_col:
                suggestions.append("Postcode nào có tổng doanh thu cao nhất?")
            if value_col and rep_col:
                suggestions.append(f"Tổng {value_col} theo từng {rep_col} là bao nhiêu?")
            if value_col and year_col and rep_col:
                suggestions.append(f"Nhân viên bán hàng nào có tổng {value_col} cao nhất trong từng {year_col}?")
        else:
            if score_col and (student_id_col or student_name_col):
                suggestions.append("How many students are in the data?")
                suggestions.append(f"Show the top 10 students by {score_col}")
            if score_col and sheet_col:
                suggestions.append(f"What is the average {score_col} for each {sheet_col}?")
                suggestions.append(f"Which {sheet_col} has the highest average {score_col}?")
            if scholarship_col:
                suggestions.append(f"How many students are in each {scholarship_col} level?")
                suggestions.append("Which students are proposed for Excellent scholarships?")
            if conduct_col:
                suggestions.append(f"What is the distribution of {conduct_col} levels?")
            if value_col and rep_col:
                suggestions.append("Show the top 10 sales representatives by revenue")
                suggestions.append("Which sales representatives are above the average sales performance?")
                suggestions.append("What is the average sales value for each sales representative?")
            if value_col and sales_rep_id_col:
                suggestions.append("Classify Sales_Rep_ID into performance groups based on sales value")
            if value_col and year_col:
                suggestions.append("What is the total sales value for each year?")
            if value_col and postcode_col:
                suggestions.append("Which postcode has the highest total sales value?")
            if value_col and rep_col:
                suggestions.append(f"What is the total {value_col} for each {rep_col}?")
            if value_col and year_col and rep_col:
                suggestions.append(f"Which sales representative has the highest total {value_col} in each {year_col}?")

    return _dedupe_suggestions(suggestions)


def _filter_schema_grounded_suggestions(lines: list[str], schema: dict, limit: int = 6) -> list[str]:
    grounded = [line for line in lines if _suggestion_is_schema_grounded(line, schema)]
    return _dedupe_suggestions(grounded, limit=limit)


def generate_suggestions(schema: dict, provider: str | None = None, language: str = "vi", semantic_schema: dict | None = None) -> list[str]:
    lang = normalize_language(language)
    semantic_schema = semantic_schema or infer_semantic_schema(schema)
    schema_specific = semantic_suggestions(schema, semantic_schema=semantic_schema, language=lang)
    schema_specific = _dedupe_suggestions(schema_specific + _schema_specific_suggestions(schema, language=lang))
    if len(schema_specific) >= 6:
        return schema_specific[:6]

    schema_str = format_schema(schema)
    output_language = "Vietnamese" if lang == "vi" else "English"
    prompt = (
        f"Database schema:\n{schema_str}\n\n"
        f"Generate 6 useful natural-language questions in {output_language} that a business analyst might ask about this data.\n"
        "Use ONLY the tables and columns listed in the schema. Do not mention concepts, dimensions, or entities that are not present.\n"
        "If the schema has Postcode but no region/area column, ask about postcode, not region or area.\n"
        "Each question must be answerable with the listed schema.\n"
        "Return only the questions, one per line, no numbering, no extra text."
    )

    selected_provider = (provider or get_default_provider()).strip().lower()
    if selected_provider == "mock":
        return generate_default_suggestions(schema, language=lang)
    if selected_provider == "auto":
        if os.getenv("DEEPSEEK_API_KEY"):
            try:
                raw = _generate_deepseek_suggestions(prompt)
                lines = [l.strip().lstrip("-0123456789. ") for l in raw.strip().split("\n") if l.strip()]
                return _dedupe_suggestions(
                    schema_specific
                    + _filter_schema_grounded_suggestions(lines, schema)
                    + generate_default_suggestions(schema, language=lang)
                )
            except Exception:
                if is_ollama_alive():
                    selected_provider = "ollama"
                else:
                    return generate_default_suggestions(schema, language=lang)
        elif is_ollama_alive():
            selected_provider = "ollama"
        else:
            return generate_default_suggestions(schema, language=lang)

    if selected_provider == "ollama":
        raw = _ollama_complete(prompt, temperature=0.2)
    elif selected_provider == "deepseek" and os.getenv("DEEPSEEK_API_KEY"):
        raw = _generate_deepseek_suggestions(prompt)
    else:
        return generate_default_suggestions(schema, language=lang)

    lines = [l.strip().lstrip("-0123456789. ") for l in raw.strip().split("\n") if l.strip()]
    return _dedupe_suggestions(
        schema_specific
        + _filter_schema_grounded_suggestions(lines, schema)
        + generate_default_suggestions(schema, language=lang)
    )


def generate_default_suggestions(schema: dict, language: str = "vi") -> list[str]:
    lang = normalize_language(language)
    semantic_schema = infer_semantic_schema(schema)
    suggestions = _dedupe_suggestions(
        semantic_suggestions(schema, semantic_schema=semantic_schema, language=lang)
        + _schema_specific_suggestions(schema, language=lang)
    )
    for table, cols in list(schema.items())[:2]:
        if lang == "vi":
            suggestions.append(f"Hiển thị toàn bộ dữ liệu từ {table}")
            suggestions.append(f"Đếm tổng số dòng trong {table}")
        else:
            suggestions.append(f"Show all data from {table}")
            suggestions.append(f"Count total rows in {table}")
        if len(cols) >= 2:
            if lang == "vi":
                suggestions.append(f"Hiển thị {cols[0]} và {cols[1]} từ {table}")
            else:
                suggestions.append(f"Show {cols[0]} and {cols[1]} from {table}")
    return suggestions[:6]
