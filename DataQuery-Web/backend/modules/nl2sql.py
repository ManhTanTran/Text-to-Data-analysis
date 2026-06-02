import os
import json
import re
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

from openai import OpenAI
from modules.labels import display_column_label, normalize_language
from modules.rag import format_examples_for_prompt, retrieve_examples


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
DEEPSEEK_MODEL = "deepseek-chat"

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))

SUPPORTED_PROVIDERS = {"ollama", "deepseek", "auto", "mock"}


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


def _clean_sql(raw: str, schema: dict | None = None) -> str:
    sql = raw.strip()
    if sql.startswith("```"):
        parts = sql.split("\n")
        sql = "\n".join(parts[1:-1] if parts[-1].strip() == "```" else parts[1:])
    sql = re.sub(r"^\s*sql\s*:\s*", "", sql, flags=re.IGNORECASE)
    sql = _repair_alias_before_aggregate(sql)
    sql = _repair_translated_identifiers(sql, schema or {})
    sql = _repair_sqlite_date_functions(sql)
    sql = _repair_date_alias_references(sql)
    sql = _repair_numeric_aggregates(sql)
    return sql.strip().rstrip(";") + ";"


def _text_key(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
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


def _finalize_generated_sql(raw: str, question: str, schema: dict, language: str) -> str:
    top_revenue_sql = _top_sales_reps_by_revenue_sql(question, schema, language)
    if top_revenue_sql:
        return top_revenue_sql
    above_average_sql = _above_average_sales_performance_sql(question, schema, language)
    if above_average_sql:
        return above_average_sql
    classification_sql = _performance_classification_sql(question, schema, language)
    if classification_sql:
        return classification_sql
    return _clean_sql(raw, schema)


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


def _repair_translated_identifiers(sql: str, schema: dict) -> str:
    """Use real schema column names before AS/GROUP BY when the model translated identifiers."""
    if not schema:
        return sql

    alias_to_source: dict[str, str] = {}
    for cols in schema.values():
        for col in cols:
            for lang in ("vi", "en"):
                alias_to_source[display_column_label(col, lang).lower()] = col
            alias_to_source[str(col).replace("_", " ").lower()] = col

    def replace_identifier(match: re.Match) -> str:
        quote, name = match.group(1), match.group(2)
        replacement = alias_to_source.get(name.lower())
        if not replacement or replacement == name:
            return match.group(0)

        prefix = sql[max(0, match.start() - 8):match.start()].upper()
        if re.search(r"\bAS\s*$", prefix):
            return match.group(0)

        return f'{quote}{replacement}{quote}'

    return re.sub(r'(["`])([^"`]+)\1', replace_identifier, sql)


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


def generate_mock_sql(schema: dict) -> str:
    table = next(iter(schema), None)
    return f'SELECT * FROM "{table}" LIMIT 10' if table else "SELECT 1"


def generate_sql(
    question: str,
    schema: dict,
    use_mock: bool = False,
    provider: str | None = None,
    language: str = "vi",
) -> tuple[str, str, str, bool, list[dict]]:
    lang = normalize_language(language)
    schema_str = format_schema(schema)
    selected_provider = (provider or get_default_provider()).strip().lower()
    rag_examples = [] if use_mock or selected_provider == "mock" else retrieve_examples(question, schema)
    rag_block = format_examples_for_prompt(rag_examples)
    alias_rules = (
        'Use Vietnamese for every display alias. Examples: "Sales Rep Name" AS "Tên nhân viên bán hàng", '
        'AVG("Value") AS "Trung bình giá trị", SUM("Value") AS "Tổng giá trị", COUNT(*) AS "Số lượng"'
        if lang == "vi"
        else 'Use English for every display alias. Examples: "Sales Rep Name" AS "Sales Rep Name", '
        'AVG("Value") AS "Average Value", SUM("Value") AS "Total Value", COUNT(*) AS "Count"'
    )
    prompt = (
        f"You are a SQLite SQL expert. Column names may contain Vietnamese or spaces.\n\n"
        f"Database schema:\n{schema_str}\n\n"
        f"{rag_block + chr(10) + chr(10) if rag_block else ''}"
        f"Write a SQL query to answer: {question}\n\n"
        "CRITICAL rules:\n"
        "- Return ONLY the SQL, no explanation, no markdown fences\n"
        '- ALWAYS wrap every table name and column name in double quotes, e.g. SELECT "Tên cột" FROM "Table"\n'
        f"- {alias_rules}\n"
        "- NEVER translate source table/column identifiers before AS; use the exact schema names on the left side, translate only display aliases after AS\n"
        "- Alias grouped/display columns too, not only aggregate columns\n"
        '- NEVER write aliases before expressions. Wrong: "Trung bình giá trị" (AVG("Value")). Correct: AVG("Value") AS "Trung bình giá trị"\n'
        "- If using GROUP BY, include the grouped column(s) in SELECT so charts can use them as the x-axis\n"
        '- If the user asks for top N sales representatives/employees by revenue, return the rep identifier/name plus SUM(TO_NUMBER("Value")) as total revenue, ORDER BY total revenue DESC, and LIMIT N\n'
        '- If the user asks to classify/segment/group sales representatives by sales performance, do NOT count groups. Aggregate by "Sales_Rep_ID", calculate SUM(TO_NUMBER("Value")), and assign performance labels with NTILE(3) or CASE: High/Medium/Low or Cao/Trung bình/Thấp\n'
        '- If the user asks which sales representatives are above average/outperforming, return the rep identifier/name, total sales, average sales, the overall average benchmark, and the difference above that benchmark\n'
        '- For AVG/SUM over value, price, amount, revenue, or sales columns, use TO_NUMBER("Column") so text-formatted numbers still calculate correctly\n'
        "- Use SQLite syntax\n"
        '- SQLite has no YEAR(), MONTH(), or DAY(); use CAST(strftime(\'%Y\', "Date column") AS INTEGER), CAST(strftime(\'%m\', "Date column") AS INTEGER), or CAST(strftime(\'%d\', "Date column") AS INTEGER)\n'
        "- Use aggregations and ORDER BY when useful"
    )

    if use_mock or selected_provider == "mock":
        return (
            _top_sales_reps_by_revenue_sql(question, schema, lang)
            or _above_average_sales_performance_sql(question, schema, lang)
            or _performance_classification_sql(question, schema, lang)
            or generate_mock_sql(schema)
        ), prompt, "mock", False, []

    if selected_provider == "auto":
        if os.getenv("DEEPSEEK_API_KEY"):
            try:
                resp = _client().chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                )
                return _finalize_generated_sql(resp.choices[0].message.content, question, schema, lang), prompt, "deepseek", bool(rag_examples), rag_examples
            except Exception:
                if is_ollama_alive():
                    return _finalize_generated_sql(_ollama_complete(prompt, temperature=0.0), question, schema, lang), prompt, "ollama", bool(rag_examples), rag_examples
                raise
        if is_ollama_alive():
            selected_provider = "ollama"
        else:
            selected_provider = "mock"

    if selected_provider == "ollama":
        return _finalize_generated_sql(_ollama_complete(prompt, temperature=0.0), question, schema, lang), prompt, "ollama", bool(rag_examples), rag_examples

    if selected_provider == "deepseek":
        resp = _client().chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return _finalize_generated_sql(resp.choices[0].message.content, question, schema, lang), prompt, "deepseek", bool(rag_examples), rag_examples

    return generate_mock_sql(schema), prompt, "mock", False, []


def _generate_deepseek_suggestions(prompt: str) -> str:
    resp = _client().chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    return resp.choices[0].message.content


def generate_suggestions(schema: dict, provider: str | None = None, language: str = "vi") -> list[str]:
    lang = normalize_language(language)
    schema_str = format_schema(schema)
    output_language = "Vietnamese" if lang == "vi" else "English"
    prompt = (
        f"Database schema:\n{schema_str}\n\n"
        f"Generate 6 useful natural-language questions in {output_language} that a business analyst might ask about this data. "
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
                return lines[:6]
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
    return lines[:6]


def generate_default_suggestions(schema: dict, language: str = "vi") -> list[str]:
    lang = normalize_language(language)
    suggestions = []
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
