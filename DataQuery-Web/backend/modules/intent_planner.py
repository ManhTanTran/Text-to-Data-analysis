import re
from typing import Any

from modules.labels import display_column_label, normalize_language
from modules.semantic_schema import (
    find_column_by_concept,
    find_column_by_terms,
    find_columns_by_role,
    infer_semantic_schema,
    text_key,
)


def _quote(identifier: str) -> str:
    return f'"{str(identifier).replace(chr(34), chr(34) + chr(34))}"'


def _literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _compact(question: str) -> str:
    return text_key(question).replace(" ", "")


def _contains_any(question: str, terms: list[str]) -> bool:
    key = text_key(question)
    compact = key.replace(" ", "")
    return any(text_key(term) in key or text_key(term).replace(" ", "") in compact for term in terms)


def _requested_aggregate(question: str) -> str | None:
    if _contains_any(question, ["trung binh", "binh quan", "average", "avg", "mean"]):
        return "AVG"
    if _contains_any(question, ["tong", "total", "sum"]):
        return "SUM"
    if _contains_any(question, ["thap nhat", "nho nhat", "minimum", "min", "lowest", "smallest"]):
        return "MIN"
    if _contains_any(question, ["cao nhat", "lon nhat", "maximum", "max", "highest", "largest"]):
        return "MAX"
    return None


def _has_measure_context(question: str) -> bool:
    return _contains_any(
        question,
        [
            "doanh thu",
            "revenue",
            "sales",
            "sales value",
            "gia tri",
            "value",
            "amount",
            "chi phi",
            "cogs",
            "cost",
            "loi nhuan",
            "profit",
            "quantity",
            "so luong",
        ],
    )


def _result(
    sql: str | None,
    intent: str,
    confidence: float = 0.0,
    constraints: list[str] | None = None,
    warnings: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "sql": sql,
        "intent": {
            "name": intent,
            "confidence": confidence,
            "details": details or {},
        },
        "constraints": constraints or [],
        "warnings": warnings or [],
        "planner_used": bool(sql),
    }


def _empty(constraints: list[str] | None = None, intent: str = "llm_fallback") -> dict[str, Any]:
    return _result(None, intent=intent, constraints=constraints or [])


def _limit_from_question(question: str, default: int = 10) -> int:
    key = text_key(question)
    match = re.search(r"\btop\s+(\d+)\b", key) or re.search(r"\b(\d+)\b", key)
    if match:
        return max(1, min(int(match.group(1)), 100))
    if _contains_any(question, ["cao nhat", "highest", "largest", "thap nhat", "lowest", "smallest", "best", "max", "min"]):
        return 1
    return default


def _first_table(schema: dict[str, list[str]]) -> tuple[str, list[str]] | tuple[None, list[str]]:
    if not schema:
        return None, []
    table = next(iter(schema))
    return table, schema[table]


def _requested_measure_columns(question: str, table: str, semantic_schema: dict[str, Any], allow_default: bool = True) -> list[str]:
    key = text_key(question)
    compact = key.replace(" ", "")
    concept_terms = [
        ("revenue", ["revenue", "doanh thu", "sales revenue", "turnover"]),
        ("cogs", ["cogs", "chi phi san xuat", "gia von", "cost of goods sold", "production cost", "manufacturing cost", "cost"]),
        ("profit", ["profit", "loi nhuan"]),
        ("sales_value", ["value", "gia tri", "sales value", "amount"]),
        ("score", ["score", "grade", "gpa", "tbchk", "diem"]),
        ("price", ["price", "gia", "don gia"]),
        ("quantity", ["quantity", "qty", "so luong"]),
    ]

    columns: list[str] = []
    for concept, terms in concept_terms:
        if any(text_key(term) in key or text_key(term).replace(" ", "") in compact for term in terms):
            found = find_column_by_concept(semantic_schema, table, concept)
            if not found and concept == "revenue":
                found = find_column_by_concept(semantic_schema, table, "sales_value")
            if not found and concept == "sales_value":
                found = find_column_by_concept(semantic_schema, table, "revenue")
            if found and found not in columns:
                columns.append(found)

    if not columns and allow_default:
        for col in find_columns_by_role(semantic_schema, table, "measure"):
            if col not in columns:
                columns.append(col)

    return columns


def _entity_columns(table: str, semantic_schema: dict[str, Any]) -> list[str]:
    columns: list[str] = []
    for role in ("entity_id", "entity_name"):
        for col in find_columns_by_role(semantic_schema, table, role):
            if col not in columns:
                columns.append(col)
    return columns


def _year_filter_sql(question: str, table: str, semantic_schema: dict[str, Any]) -> str | None:
    match = re.search(r"\b((?:19|20)\d{2})\b", question or "")
    if not match:
        return None
    year_col = find_column_by_concept(semantic_schema, table, "year") or find_column_by_concept(semantic_schema, table, "time")
    if not year_col:
        return None
    year = match.group(1)
    if text_key(year_col) == "year":
        return f"{_quote(year_col)} = {_literal(year)}"
    return f"strftime('%Y', {_quote(year_col)}) = {_literal(year)}"


def _where_clause(conditions: list[str]) -> str:
    clean_conditions = [condition for condition in conditions if condition]
    return "" if not clean_conditions else "WHERE " + " AND ".join(clean_conditions) + "\n"


def _label_minimum(column: str, language: str) -> str:
    if normalize_language(language) == "en":
        return f"Minimum {display_column_label(column, 'en')}"
    return f"Nhỏ nhất {display_column_label(column, 'vi').lower()}"


def _label_maximum(column: str, language: str) -> str:
    if normalize_language(language) == "en":
        return f"Maximum {display_column_label(column, 'en')}"
    return f"Lớn nhất {display_column_label(column, 'vi').lower()}"


def _status_level_from_question(question: str) -> str | None:
    key = text_key(question)
    compact = key.replace(" ", "")
    if "xuat sac" in key or "excellent" in key:
        return "Xuất sắc"
    if "khong du tin chi" in key or "khongdutinchi" in compact:
        return "Không đủ tín chỉ"
    if "khong dat" in key or "khongdat" in compact or "not qualified" in key:
        return "Không đạt"
    if re.search(r"\bgioi\b", key) or "good" in key:
        return "Giỏi"
    if re.search(r"\bkha\b", key):
        return "Khá"
    if "high" in key:
        return "High"
    if "medium" in key:
        return "Medium"
    if "low" in key:
        return "Low"
    return None


def _label_total(column: str, language: str) -> str:
    if normalize_language(language) == "en":
        return f"Total {display_column_label(column, 'en')}"
    return f"Tổng {display_column_label(column, 'vi').lower()}"


def _label_average(column: str, language: str) -> str:
    if normalize_language(language) == "en":
        return f"Average {display_column_label(column, 'en')}"
    return f"Trung bình {display_column_label(column, 'vi').lower()}"


def _label_for_aggregate(column: str, aggregate_func: str, language: str) -> str:
    aggregate_func = aggregate_func.upper()
    if aggregate_func == "AVG":
        return _label_average(column, language)
    if aggregate_func == "MIN":
        return _label_minimum(column, language)
    if aggregate_func == "MAX":
        return _label_maximum(column, language)
    return _label_total(column, language)


def _time_grain(question: str) -> tuple[str, str]:
    key = text_key(question)
    if any(term in key for term in ["nam", "year", "annual"]):
        return "%Y", "year"
    return "%Y-%m", "month"


def _plan_trend(question: str, schema: dict[str, list[str]], semantic_schema: dict[str, Any], language: str) -> dict[str, Any] | None:
    if not _contains_any(question, ["tang giam", "tang hay giam", "xu huong", "bien dong", "thay doi", "theo thoi gian", "trend", "over time", "change over time", "increase", "decrease"]):
        return None

    table, _ = _first_table(schema)
    if not table:
        return None
    date_col = find_column_by_concept(semantic_schema, table, "time", "year")
    if not date_col:
        return None
    measures = _requested_measure_columns(question, table, semantic_schema)
    if not measures:
        return None
    aggregate_func = _requested_aggregate(question) or "SUM"

    fmt, grain = _time_grain(question)
    time_expr = f"strftime('{fmt}', {_quote(date_col)})" if fmt != "%Y" or text_key(date_col) != "year" else _quote(date_col)
    time_label = display_column_label(date_col, language) if text_key(date_col) == "year" else ("Time" if normalize_language(language) == "en" else "Thời gian")
    secondary_group = None
    if _contains_any(question, ["nhan vien", "sales rep", "sales representative", "employee"]):
        secondary_group = find_column_by_concept(semantic_schema, table, "sales_rep_name") or find_column_by_concept(semantic_schema, table, "sales_rep_id")

    select_columns = []
    group_terms = []
    order_terms = []
    if secondary_group and secondary_group != date_col:
        secondary_label = display_column_label(secondary_group, language)
        select_columns.append(f"  {_quote(secondary_group)} AS {_quote(secondary_label)}")
        group_terms.append(_quote(secondary_group))
        order_terms.append(_quote(secondary_label))
    select_columns.append(f"  {time_expr} AS {_quote(time_label)}")
    group_terms.append(time_expr)
    order_terms.append(_quote(time_label))
    for col in measures:
        select_columns.append(f"  {aggregate_func}(TO_NUMBER({_quote(col)})) AS {_quote(_label_for_aggregate(col, aggregate_func, language))}")

    sql = (
        "SELECT\n"
        + ",\n".join(select_columns)
        + f"\nFROM {_quote(table)}\n"
        + f"WHERE {_quote(date_col)} IS NOT NULL\n"
        + "GROUP BY "
        + ", ".join(group_terms)
        + "\nORDER BY "
        + ", ".join(f"{term} ASC" for term in order_terms)
        + ";"
    )
    return _result(
        sql,
        "trend_over_time",
        0.95,
        ["Group time-trend questions by a time period and return one row per period."],
        details={"table": table, "time_column": date_col, "measures": measures, "grain": grain, "aggregate": aggregate_func},
    )


def _plan_status_filter(question: str, schema: dict[str, list[str]], semantic_schema: dict[str, Any], language: str) -> dict[str, Any] | None:
    if not _contains_any(question, ["hoc bong", "de xuat", "scholarship", "status", "trang thai"]) or not _contains_any(question, ["sinh vien", "student", "ai", "who", "nao"]):
        return None
    table, _ = _first_table(schema)
    if not table:
        return None
    status_columns = find_columns_by_role(semantic_schema, table, "status")
    status_col = find_column_by_concept(semantic_schema, table, "scholarship") or (status_columns[0] if status_columns else None)
    if not status_col:
        return None

    level = _status_level_from_question(question)
    entity_cols = _entity_columns(table, semantic_schema)
    measures = _requested_measure_columns(question, table, semantic_schema, allow_default=False)
    score_col = find_column_by_concept(semantic_schema, table, "score")
    if score_col and score_col not in measures:
        measures.append(score_col)
    category_cols = [find_column_by_concept(semantic_schema, table, "sheet")]

    select_cols: list[str] = []
    for col in category_cols + entity_cols:
        if col and col not in select_cols:
            select_cols.append(col)
    for col in measures:
        if col not in select_cols:
            select_cols.append(col)
    if status_col not in select_cols:
        select_cols.append(status_col)
    if len(select_cols) <= 1:
        return None

    select_sql: list[str] = []
    for col in select_cols:
        if col in measures:
            select_sql.append(f"  TO_NUMBER({_quote(col)}) AS {_quote(display_column_label(col, language))}")
        else:
            select_sql.append(f"  {_quote(col)} AS {_quote(display_column_label(col, language))}")

    where_sql = f"WHERE {_quote(status_col)} = {_literal(level)}" if level else f"WHERE {_quote(status_col)} IS NOT NULL AND TRIM({_quote(status_col)}) <> ''"
    order_terms = []
    if score_col:
        order_terms.append(f"{_quote(display_column_label(score_col, language))} DESC")
    for col in entity_cols:
        order_terms.append(f"{_quote(display_column_label(col, language))} ASC")
    order_sql = ", ".join(order_terms) if order_terms else f"{_quote(display_column_label(status_col, language))} ASC"

    sql = "SELECT\n" + ",\n".join(select_sql) + f"\nFROM {_quote(table)}\n{where_sql}\nORDER BY {order_sql};"
    return _result(
        sql,
        "filter_status_entities",
        0.94,
        ["Entity/status questions must return entity columns and filter the status column."],
        details={"table": table, "status_column": status_col, "level": level, "entity_columns": entity_cols},
    )


def _plan_above_average(question: str, schema: dict[str, list[str]], semantic_schema: dict[str, Any], language: str) -> dict[str, Any] | None:
    wants_above_average = _contains_any(
        question,
        [
            "vuot troi",
            "vuot binh quan",
            "tren binh quan",
            "cao hon binh quan",
            "so voi binh quan",
            "above average",
            "above the average",
            "higher than average",
            "outperform",
            "outstanding",
        ],
    )
    wants_performance = _contains_any(question, ["hieu suat", "kinh doanh", "ban hang", "sales", "performance", "doanh thu", "revenue"])
    if not (wants_above_average and wants_performance):
        return None

    table, _ = _first_table(schema)
    if not table:
        return None
    entity_cols = _entity_columns(table, semantic_schema)
    measures = _requested_measure_columns(question, table, semantic_schema)
    if not entity_cols or not measures:
        return None

    measure = measures[0]
    total_label = _label_total(measure, language)
    average_label = _label_average(measure, language)
    benchmark_label = f"Average {total_label}" if normalize_language(language) == "en" else f"Bình quân {total_label.lower()}"
    diff_label = "Difference Above Average" if normalize_language(language) == "en" else "Chênh lệch so với bình quân"
    rate_label = "Above Average Rate (%)" if normalize_language(language) == "en" else "Tỷ lệ vượt bình quân (%)"
    count_label = "Record Count" if normalize_language(language) == "en" else "Số dòng"

    source_entity_select = []
    group_columns = []
    final_entity_select = []
    for col in entity_cols:
        label = display_column_label(col, language)
        source_entity_select.append(f"    {_quote(col)} AS {_quote(label)}")
        group_columns.append(_quote(col))
        final_entity_select.append(f'  "entity_performance".{_quote(label)}')

    sql = (
        'WITH "entity_performance" AS (\n'
        "  SELECT\n"
        + ",\n".join(source_entity_select)
        + ",\n"
        + f"    SUM(TO_NUMBER({_quote(measure)})) AS {_quote(total_label)},\n"
        + f"    AVG(TO_NUMBER({_quote(measure)})) AS {_quote(average_label)},\n"
        + f"    COUNT(*) AS {_quote(count_label)}\n"
        + f"  FROM {_quote(table)}\n"
        + f"  WHERE TO_NUMBER({_quote(measure)}) IS NOT NULL\n"
        + "  GROUP BY "
        + ", ".join(group_columns)
        + "\n"
        + "),\n"
        + '"benchmark" AS (\n'
        + f"  SELECT AVG({_quote(total_label)}) AS {_quote(benchmark_label)}\n"
        + '  FROM "entity_performance"\n'
        + ")\n"
        + "SELECT\n"
        + ",\n".join(final_entity_select)
        + ",\n"
        + f'  "entity_performance".{_quote(total_label)},\n'
        + f'  "entity_performance".{_quote(average_label)},\n'
        + f'  "entity_performance".{_quote(count_label)},\n'
        + f'  "benchmark".{_quote(benchmark_label)},\n'
        + f'  "entity_performance".{_quote(total_label)} - "benchmark".{_quote(benchmark_label)} AS {_quote(diff_label)},\n'
        + f'  ROUND(("entity_performance".{_quote(total_label)} / NULLIF("benchmark".{_quote(benchmark_label)}, 0) - 1) * 100, 2) AS {_quote(rate_label)}\n'
        + 'FROM "entity_performance"\n'
        + 'CROSS JOIN "benchmark"\n'
        + f'WHERE "entity_performance".{_quote(total_label)} > "benchmark".{_quote(benchmark_label)}\n'
        + f'ORDER BY "entity_performance".{_quote(total_label)} DESC;'
    )
    return _result(
        sql,
        "above_average_entities",
        0.9,
        ["Above-average entity questions must return entity columns, the benchmark, and the difference above the benchmark."],
        details={"table": table, "entity_columns": entity_cols, "measure": measure},
    )


def _plan_classification(question: str, schema: dict[str, list[str]], semantic_schema: dict[str, Any], language: str) -> dict[str, Any] | None:
    wants_classification = _contains_any(question, ["phan loai", "classify", "segment", "bucket", "nhom", "group"])
    wants_performance = _contains_any(question, ["hieu suat", "performance", "ban hang", "sales", "kinh doanh", "doanh thu", "revenue"])
    if not (wants_classification and wants_performance):
        return None

    table, _ = _first_table(schema)
    if not table:
        return None
    entity_cols = _entity_columns(table, semantic_schema)
    measures = _requested_measure_columns(question, table, semantic_schema)
    if not entity_cols or not measures:
        return None

    measure = measures[0]
    total_label = _label_total(measure, language)
    average_label = _label_average(measure, language)
    count_label = "Record Count" if normalize_language(language) == "en" else "Số dòng"
    group_label = "Performance Group" if normalize_language(language) == "en" else "Nhóm hiệu suất"
    high, medium, low = ("High", "Medium", "Low") if normalize_language(language) == "en" else ("Cao", "Trung bình", "Thấp")

    source_entity_select = []
    group_columns = []
    final_entity_select = []
    for col in entity_cols:
        label = display_column_label(col, language)
        source_entity_select.append(f"    {_quote(col)} AS {_quote(label)}")
        group_columns.append(_quote(col))
        final_entity_select.append(f'  "ranked".{_quote(label)}')

    sql = (
        'WITH "entity_performance" AS (\n'
        "  SELECT\n"
        + ",\n".join(source_entity_select)
        + ",\n"
        + f"    SUM(TO_NUMBER({_quote(measure)})) AS {_quote(total_label)},\n"
        + f"    AVG(TO_NUMBER({_quote(measure)})) AS {_quote(average_label)},\n"
        + f"    COUNT(*) AS {_quote(count_label)}\n"
        + f"  FROM {_quote(table)}\n"
        + f"  WHERE TO_NUMBER({_quote(measure)}) IS NOT NULL\n"
        + "  GROUP BY "
        + ", ".join(group_columns)
        + "\n"
        + "),\n"
        + '"ranked" AS (\n'
        + "  SELECT\n"
        + '    "entity_performance".*,\n'
        + f'    NTILE(3) OVER (ORDER BY {_quote(total_label)} DESC) AS "_performance_bucket"\n'
        + '  FROM "entity_performance"\n'
        + ")\n"
        + "SELECT\n"
        + ",\n".join(final_entity_select)
        + ",\n"
        + f'  "ranked".{_quote(total_label)},\n'
        + f'  "ranked".{_quote(average_label)},\n'
        + f'  "ranked".{_quote(count_label)},\n'
        + f"  CASE \"_performance_bucket\" WHEN 1 THEN {_literal(high)} WHEN 2 THEN {_literal(medium)} ELSE {_literal(low)} END AS {_quote(group_label)}\n"
        + 'FROM "ranked"\n'
        + f'ORDER BY "ranked".{_quote(total_label)} DESC;'
    )
    return _result(
        sql,
        "classify_entities",
        0.9,
        ["Classification questions should aggregate by entity and assign High/Medium/Low groups instead of counting group names."],
        details={"table": table, "entity_columns": entity_cols, "measure": measure},
    )


def _mentioned_group_column(question: str, table: str, semantic_schema: dict[str, Any]) -> str | None:
    key = text_key(question)
    compact = key.replace(" ", "")
    if _contains_any(question, ["postcode", "postal", "zip"]):
        return find_column_by_concept(semantic_schema, table, "postcode")
    if _contains_any(question, ["nhan vien", "sales rep", "sales representative", "employee"]):
        return find_column_by_concept(semantic_schema, table, "sales_rep_name") or find_column_by_concept(semantic_schema, table, "sales_rep_id")
    if _contains_any(question, ["nam", "year"]):
        return find_column_by_concept(semantic_schema, table, "year") or find_column_by_concept(semantic_schema, table, "time")
    table_columns = semantic_schema.get("tables", {}).get(table, {}).get("columns", {})
    for col, info in table_columns.items():
        aliases = [col] + list(info.get("aliases", []))
        for alias in aliases:
            alias_key = text_key(alias)
            if alias_key and (alias_key in key or alias_key.replace(" ", "") in compact):
                return col
    if _contains_any(question, ["sheet", "nganh", "major", "department"]):
        return find_column_by_concept(semantic_schema, table, "sheet")
    return None


def _plan_group_aggregate(question: str, schema: dict[str, list[str]], semantic_schema: dict[str, Any], language: str) -> dict[str, Any] | None:
    table, _ = _first_table(schema)
    if not table:
        return None
    group_col = _mentioned_group_column(question, table, semantic_schema)
    wants_group = bool(group_col) and _contains_any(
        question,
        ["theo", "tung", "moi", "for each", "each", " by ", "group by", "phan bo", "distribution", "liet ke", "list"],
    )
    aggregate_func = _requested_aggregate(question)
    if not aggregate_func and _contains_any(question, ["diem", "score", "gpa", "tbchk"]):
        aggregate_func = "AVG"
    elif not aggregate_func and _has_measure_context(question):
        aggregate_func = "SUM"
    if not wants_group or not aggregate_func:
        return None

    measures = _requested_measure_columns(question, table, semantic_schema)
    if not group_col or not measures:
        return None

    select_cols = [f"  {_quote(group_col)} AS {_quote(display_column_label(group_col, language))}"]
    for col in measures:
        label = _label_for_aggregate(col, aggregate_func, language)
        select_cols.append(f"  {aggregate_func}(TO_NUMBER({_quote(col)})) AS {_quote(label)}")

    order_label = _label_for_aggregate(measures[0], aggregate_func, language)
    order_direction = "ASC" if aggregate_func == "MIN" else "DESC"
    sql = (
        "SELECT\n"
        + ",\n".join(select_cols)
        + f"\nFROM {_quote(table)}\n"
        + _where_clause([f"TO_NUMBER({_quote(measures[0])}) IS NOT NULL", _year_filter_sql(question, table, semantic_schema)])
        + f"GROUP BY {_quote(group_col)}\n"
        + f"ORDER BY {_quote(order_label)} {order_direction};"
    )
    return _result(
        sql,
        "group_aggregate",
        0.9,
        ["Grouped aggregate questions must include the group column in SELECT and GROUP BY."],
        details={"table": table, "group_column": group_col, "measures": measures, "aggregate": aggregate_func},
    )


def _plan_distribution_count(question: str, schema: dict[str, list[str]], semantic_schema: dict[str, Any], language: str) -> dict[str, Any] | None:
    if not _contains_any(question, ["phan bo", "distribution", "theo tung muc", "theo moi muc", "by level", "each level"]):
        return None

    table, _ = _first_table(schema)
    if not table:
        return None
    group_col = _mentioned_group_column(question, table, semantic_schema)
    if not group_col:
        return None

    entity_col = None
    if find_column_by_concept(semantic_schema, table, "student_id"):
        entity_col = find_column_by_concept(semantic_schema, table, "student_id")
    elif find_column_by_concept(semantic_schema, table, "sales_rep_id"):
        entity_col = find_column_by_concept(semantic_schema, table, "sales_rep_id")

    if normalize_language(language) == "en":
        count_label = "Student Count" if find_column_by_concept(semantic_schema, table, "student_id") else "Count"
    else:
        count_label = "Số lượng sinh viên" if find_column_by_concept(semantic_schema, table, "student_id") else "Số lượng"

    count_expr = f"COUNT(DISTINCT {_quote(entity_col)})" if entity_col else "COUNT(*)"
    sql = (
        "SELECT\n"
        + f"  {_quote(group_col)} AS {_quote(display_column_label(group_col, language))},\n"
        + f"  {count_expr} AS {_quote(count_label)}\n"
        + f"FROM {_quote(table)}\n"
        + f"WHERE {_quote(group_col)} IS NOT NULL AND TRIM({_quote(group_col)}) <> ''\n"
        + f"GROUP BY {_quote(group_col)}\n"
        + f"ORDER BY {_quote(count_label)} DESC;"
    )
    return _result(
        sql,
        "distribution_count",
        0.9,
        ["Distribution-by-level questions should count rows/entities per level, not average unrelated measures."],
        details={"table": table, "group_column": group_col, "count_column": entity_col},
    )


def _plan_group_count(question: str, schema: dict[str, list[str]], semantic_schema: dict[str, Any], language: str) -> dict[str, Any] | None:
    if not _contains_any(question, ["bao nhieu", "count", "how many", "so luong", "dem"]):
        return None
    if not _contains_any(question, ["theo", "tung", "moi", "for each", "each", " by ", "group by"]):
        return None

    table, _ = _first_table(schema)
    if not table:
        return None
    group_col = _mentioned_group_column(question, table, semantic_schema)
    if not group_col:
        return None

    label = "Record Count" if normalize_language(language) == "en" else "Số dòng"
    if _contains_any(question, ["giao dich", "transaction"]):
        label = "Transaction Count" if normalize_language(language) == "en" else "Số giao dịch"

    sql = (
        "SELECT\n"
        + f"  {_quote(group_col)} AS {_quote(display_column_label(group_col, language))},\n"
        + f"  COUNT(*) AS {_quote(label)}\n"
        + f"FROM {_quote(table)}\n"
        + _where_clause([_year_filter_sql(question, table, semantic_schema)])
        + f"GROUP BY {_quote(group_col)}\n"
        + f"ORDER BY {_quote(label)} DESC;"
    )
    return _result(
        sql,
        "group_count",
        0.86,
        ["Grouped count questions should include the group column and COUNT(*)."],
        details={"table": table, "group_column": group_col},
    )


def _plan_count_rows(question: str, schema: dict[str, list[str]], semantic_schema: dict[str, Any], language: str) -> dict[str, Any] | None:
    if not _contains_any(question, ["bao nhieu", "count", "how many", "so luong", "dem"]):
        return None
    if not _contains_any(question, ["dong", "record", "row", "giao dich", "transaction"]):
        return None
    if _contains_any(question, ["theo", "tung", "moi", "for each", "each", " by ", "group by"]):
        return None

    table, _ = _first_table(schema)
    if not table:
        return None
    label = "Record Count" if normalize_language(language) == "en" else "Số dòng"
    if _contains_any(question, ["giao dich", "transaction"]):
        label = "Transaction Count" if normalize_language(language) == "en" else "Số giao dịch"
    sql = f"SELECT COUNT(*) AS {_quote(label)}\nFROM {_quote(table)};"
    return _result(
        sql,
        "count_rows",
        0.86,
        ["Row-count questions should use COUNT(*), not SELECT *."],
        details={"table": table},
    )


def _plan_top_bottom_gap(question: str, schema: dict[str, list[str]], semantic_schema: dict[str, Any], language: str) -> dict[str, Any] | None:
    if not _contains_any(question, ["chenh lech", "gap", "difference"]) or not _contains_any(question, ["cao nhat", "thap nhat", "top", "bottom", "highest", "lowest"]):
        return None

    table, _ = _first_table(schema)
    if not table:
        return None
    entity_cols = _entity_columns(table, semantic_schema)
    measures = _requested_measure_columns(question, table, semantic_schema)
    if not entity_cols or not measures:
        return None

    measure = measures[0]
    total_label = _label_total(measure, language)
    diff_label = "Difference" if normalize_language(language) == "en" else "Chênh lệch"
    group_sql = ", ".join(_quote(col) for col in entity_cols)
    sql = (
        'WITH "entity_totals" AS (\n'
        + f"  SELECT SUM(TO_NUMBER({_quote(measure)})) AS {_quote(total_label)}\n"
        + f"  FROM {_quote(table)}\n"
        + _where_clause([f"TO_NUMBER({_quote(measure)}) IS NOT NULL", _year_filter_sql(question, table, semantic_schema)])
        + f"  GROUP BY {group_sql}\n"
        + ")\n"
        + f"SELECT MAX({_quote(total_label)}) - MIN({_quote(total_label)}) AS {_quote(diff_label)}\n"
        + 'FROM "entity_totals";'
    )
    return _result(
        sql,
        "top_bottom_gap",
        0.86,
        ["Top-bottom gap questions should compare max and min aggregate values."],
        details={"table": table, "entity_columns": entity_cols, "measure": measure},
    )


def _plan_list_projection(question: str, schema: dict[str, list[str]], semantic_schema: dict[str, Any], language: str) -> dict[str, Any] | None:
    if not _contains_any(question, ["liet ke", "hien thi", "list", "show", "nao", "which"]):
        return None
    if _contains_any(question, ["tong", "total", "sum", "trung binh", "average", "top", "cao nhat", "thap nhat", "count", "bao nhieu", "doanh thu", "revenue", "gia tri", "sales value"]):
        return None

    table, _ = _first_table(schema)
    if not table:
        return None

    target_cols: list[str] = []
    if _contains_any(question, ["postcode", "postal", "zip"]):
        col = find_column_by_concept(semantic_schema, table, "postcode")
        if col:
            target_cols.append(col)
    elif _contains_any(question, ["ma nhan vien", "sales rep id", "employee id"]):
        col = find_column_by_concept(semantic_schema, table, "sales_rep_id")
        if col:
            target_cols.append(col)
    elif _contains_any(question, ["ten nhan vien", "sales rep name", "employee name", "nhan vien"]):
        col = find_column_by_concept(semantic_schema, table, "sales_rep_name") or find_column_by_concept(semantic_schema, table, "sales_rep_id")
        if col:
            target_cols.append(col)
    if not target_cols:
        return None

    filters: list[str] = []
    name_col = find_column_by_concept(semantic_schema, table, "sales_rep_name")
    if name_col:
        names = [
            token
            for token in re.findall(r"\b[A-Z][A-Za-z]+\b", question or "")
            if text_key(token) not in {"show", "list", "top", "which"}
        ]
        if names:
            filters.append(f"{_quote(name_col)} = {_literal(names[-1])}")

    select_cols = [f"  {_quote(col)} AS {_quote(display_column_label(col, language))}" for col in target_cols]
    sql = (
        "SELECT DISTINCT\n"
        + ",\n".join(select_cols)
        + f"\nFROM {_quote(table)}\n"
        + _where_clause(filters)
        + f"ORDER BY {_quote(display_column_label(target_cols[0], language))} ASC;"
    )
    return _result(
        sql,
        "list_projection",
        0.82,
        ["List questions should project only requested existing columns and use filters only when stated."],
        details={"table": table, "columns": target_cols},
    )


def _plan_count_distinct(question: str, schema: dict[str, list[str]], semantic_schema: dict[str, Any], language: str) -> dict[str, Any] | None:
    if not _contains_any(question, ["bao nhieu", "so luong", "dem", "count", "how many", "number of"]):
        return None
    if _contains_any(question, ["tong", "total", "sum", "trung binh", "average", "avg", "min", "max", "cao nhat", "thap nhat", "doanh thu", "revenue", "gia tri"]):
        return None
    if _contains_any(question, ["dong", "record", "row", "giao dich", "transaction"]):
        return None

    table, _ = _first_table(schema)
    if not table:
        return None
    entity_cols = _entity_columns(table, semantic_schema)
    if not entity_cols:
        return None

    requested_entity = None
    if _contains_any(question, ["sinh vien", "student", "sv"]):
        requested_entity = find_column_by_concept(semantic_schema, table, "student_id") or find_column_by_concept(semantic_schema, table, "student_name")
    elif _contains_any(question, ["nhan vien", "sales rep", "employee"]):
        requested_entity = find_column_by_concept(semantic_schema, table, "sales_rep_id") or find_column_by_concept(semantic_schema, table, "sales_rep_name")
    count_col = requested_entity or entity_cols[0]
    label = "Count" if normalize_language(language) == "en" else "Số lượng"
    if find_column_by_concept(semantic_schema, table, "student_id") == count_col:
        label = "Student Count" if normalize_language(language) == "en" else "Số lượng sinh viên"
    if find_column_by_concept(semantic_schema, table, "sales_rep_id") == count_col:
        label = "Employee Count" if normalize_language(language) == "en" else "Số lượng nhân viên"

    sql = f"SELECT COUNT(DISTINCT {_quote(count_col)}) AS {_quote(label)}\nFROM {_quote(table)};"
    return _result(
        sql,
        "count_distinct_entity",
        0.9,
        ["Count entity questions should use COUNT(DISTINCT entity_id/name), not COUNT(*)."],
        details={"table": table, "count_column": count_col},
    )


def _plan_top_n(question: str, schema: dict[str, list[str]], semantic_schema: dict[str, Any], language: str) -> dict[str, Any] | None:
    if not _contains_any(question, ["top", "cao nhat", "highest", "largest", "best", "max", "thap nhat", "lowest", "smallest", "min"]):
        return None
    has_target = _contains_any(question, ["top", "nhan vien", "sales rep", "sales representative", "employee", "postcode", "postal", "zip", "ai", "nao", "which"])
    if not has_target and _contains_any(question, ["la bao nhieu", "how much"]):
        return None
    table, _ = _first_table(schema)
    if not table:
        return None
    measures = _requested_measure_columns(question, table, semantic_schema)
    target_cols: list[str] = []
    if _contains_any(question, ["postcode", "postal", "zip"]):
        postcode_col = find_column_by_concept(semantic_schema, table, "postcode")
        if postcode_col:
            target_cols.append(postcode_col)
    if not target_cols:
        target_cols = _entity_columns(table, semantic_schema)
    if not measures or not target_cols:
        return None

    limit = _limit_from_question(question)
    measure = measures[0]
    label = display_column_label(measure, language) if find_column_by_concept(semantic_schema, table, "score") == measure else _label_total(measure, language)
    select_cols = [f"  {_quote(col)} AS {_quote(display_column_label(col, language))}" for col in target_cols]
    order_direction = "ASC" if _contains_any(question, ["thap nhat", "lowest", "smallest", "min"]) else "DESC"
    if find_column_by_concept(semantic_schema, table, "score") == measure:
        select_cols.append(f"  TO_NUMBER({_quote(measure)}) AS {_quote(label)}")
        group_sql = ""
        order_sql = f"ORDER BY {_quote(label)} {order_direction}\nLIMIT {limit};"
    else:
        select_cols.append(f"  SUM(TO_NUMBER({_quote(measure)})) AS {_quote(label)}")
        group_sql = "GROUP BY " + ", ".join(_quote(col) for col in target_cols) + "\n"
        order_sql = f"ORDER BY {_quote(label)} {order_direction}\nLIMIT {limit};"
    sql = (
        "SELECT\n"
        + ",\n".join(select_cols)
        + f"\nFROM {_quote(table)}\n"
        + f"WHERE TO_NUMBER({_quote(measure)}) IS NOT NULL\n"
        + group_sql
        + order_sql
    )
    return _result(
        sql,
        "top_n_entities",
        0.88,
        ["Top-N entity questions should return entity columns, order by the requested measure, and limit N."],
        details={"table": table, "entity_columns": target_cols, "measure": measure, "limit": limit, "order": order_direction},
    )


def _plan_single_aggregate(question: str, schema: dict[str, list[str]], semantic_schema: dict[str, Any], language: str) -> dict[str, Any] | None:
    func = _requested_aggregate(question)
    if not func and _contains_any(question, ["diem", "score", "gpa", "tbchk"]):
        func = "AVG"
    elif not func and _has_measure_context(question):
        func = "SUM"
    if not func:
        return None
    if _contains_any(question, ["theo tung", "theo moi", "for each", "by each", "tang giam", "theo thoi gian", "over time"]):
        return None

    table, _ = _first_table(schema)
    if not table:
        return None
    measures = _requested_measure_columns(question, table, semantic_schema, allow_default=True)
    if not measures:
        return None

    select_cols = []
    for col in measures:
        label = _label_for_aggregate(col, func, language)
        select_cols.append(f"  {func}(TO_NUMBER({_quote(col)})) AS {_quote(label)}")
    sql = "SELECT\n" + ",\n".join(select_cols) + f"\nFROM {_quote(table)}\n" + _where_clause([_year_filter_sql(question, table, semantic_schema)]).rstrip() + ";"
    return _result(
        sql,
        "single_aggregate",
        0.82,
        ["Single aggregate questions should use only measures present in the schema."],
        details={"table": table, "measures": measures, "aggregate": func},
    )


def plan_sql(question: str, schema: dict[str, list[str]], semantic_schema: dict[str, Any] | None = None, language: str = "vi") -> dict[str, Any]:
    semantic_schema = semantic_schema or infer_semantic_schema(schema)
    constraints = [
        "Use only source columns that exist in the schema.",
        "Map semantic synonyms to real columns, e.g. COGS for production cost when present.",
        "Do not invent concrete dates or years not present in the user question.",
    ]

    planners = [
        _plan_trend,
        _plan_status_filter,
        _plan_above_average,
        _plan_classification,
        _plan_top_bottom_gap,
        _plan_top_n,
        _plan_distribution_count,
        _plan_group_aggregate,
        _plan_group_count,
        _plan_count_rows,
        _plan_list_projection,
        _plan_single_aggregate,
        _plan_count_distinct,
    ]
    for planner in planners:
        result = planner(question, schema, semantic_schema, language)
        if result and result.get("sql"):
            result["constraints"] = constraints + result.get("constraints", [])
            return result

    return _empty(constraints=constraints)
