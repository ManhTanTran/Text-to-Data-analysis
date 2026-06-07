import re
from typing import Any

from modules.semantic_schema import find_columns_by_role, text_key


def _source_identifiers(sql: str) -> list[str]:
    select_match = re.search(r"\bSELECT\b(?P<body>.*?)\bFROM\b", sql, flags=re.IGNORECASE | re.DOTALL)
    if not select_match:
        return []
    source_part = re.sub(r"\bAS\b\s+(\"[^\"]+\"|`[^`]+`|\[[^\]]+\])", "", select_match.group("body"), flags=re.IGNORECASE)
    matches = re.findall(r'"([^"]+)"|`([^`]+)`|\[([^\]]+)\]', source_part)
    return [next(part for part in parts if part) for parts in matches]


def validate_sql_shape(
    sql: str,
    question: str,
    schema: dict[str, list[str]],
    semantic_schema: dict[str, Any] | None = None,
    intent: dict[str, Any] | None = None,
) -> list[str]:
    warnings: list[str] = []
    valid_columns = {text_key(column) for columns in schema.values() for column in columns}
    valid_tables = {text_key(table) for table in schema}
    source_ids = _source_identifiers(sql)
    unknown = [name for name in source_ids if text_key(name) not in valid_columns and text_key(name) not in valid_tables]
    if unknown:
        warnings.append("SQL still references unknown source identifiers: " + ", ".join(sorted(set(unknown))))

    key = text_key(question)
    intent_name = (intent or {}).get("name", "")

    if intent_name == "trend_over_time" or any(term in key for term in ["tang giam", "theo thoi gian", "over time", "trend"]):
        if "group by" not in sql.lower():
            warnings.append("Trend question should return one row per time period with GROUP BY.")

    if any(term in key for term in ["theo tung", "for each", "by each"]) and "group by" not in sql.lower():
        warnings.append("Grouped question should include GROUP BY.")

    if semantic_schema and any(term in key for term in [" ai ", " nao ", " who ", "sinh vien", "student", "nhan vien", "employee"]):
        entity_columns: set[str] = set()
        for table in schema:
            entity_columns.update(find_columns_by_role(semantic_schema, table, "entity_id"))
            entity_columns.update(find_columns_by_role(semantic_schema, table, "entity_name"))
        sql_key = text_key(sql)
        if entity_columns and not any(text_key(column) in sql_key for column in entity_columns):
            warnings.append("Entity question should return an entity id or name column.")

    return warnings
