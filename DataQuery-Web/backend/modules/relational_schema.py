import re
import sqlite3
import unicodedata
from typing import Any


def text_key(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _is_id_like(column: str) -> bool:
    key = text_key(column)
    tokens = key.split()
    return (
        "id" in tokens
        or key.endswith(" id")
        or key.endswith("_id")
        or "code" in tokens
        or "ma" in tokens
        or key.startswith("ma ")
        or key.startswith("mã ")
    )


def _column_name_similarity(left: str, right: str) -> float:
    left_key = text_key(left)
    right_key = text_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    left_tokens = set(left_key.split())
    right_tokens = set(right_key.split())
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    if left_key.endswith(right_key) or right_key.endswith(left_key):
        overlap = max(overlap, 0.75)
    if left_key.replace(" ", "").endswith(right_key.replace(" ", "")):
        overlap = max(overlap, 0.8)
    return overlap


def _fetch_values(conn: sqlite3.Connection, table: str, column: str, limit: int = 5000) -> list[Any]:
    sql = (
        f"SELECT {_quote(column)} FROM {_quote(table)} "
        f"WHERE {_quote(column)} IS NOT NULL AND TRIM(CAST({_quote(column)} AS TEXT)) <> '' "
        f"LIMIT {int(limit)}"
    )
    try:
        return [row[0] for row in conn.execute(sql).fetchall()]
    except Exception:
        return []


def _table_row_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0])
    except Exception:
        return 0


def _column_profile(conn: sqlite3.Connection, table: str, column: str, row_count: int) -> dict[str, Any]:
    values = _fetch_values(conn, table, column)
    normalized = [str(value).strip().lower() for value in values if str(value).strip()]
    distinct_values = set(normalized)
    non_null_count = len(normalized)
    distinct_count = len(distinct_values)
    uniqueness_ratio = distinct_count / max(1, non_null_count)
    non_null_ratio = non_null_count / max(1, row_count)
    return {
        "non_null_count": non_null_count,
        "distinct_count": distinct_count,
        "non_null_ratio": round(non_null_ratio, 4),
        "uniqueness_ratio": round(uniqueness_ratio, 4),
        "id_like": _is_id_like(column),
        "sample_values": list(dict.fromkeys(normalized[:10])),
        "_value_set": distinct_values,
    }


def _primary_key_candidates(table: str, columns: list[str], profiles: dict[str, dict[str, Any]], row_count: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for column in columns:
        profile = profiles[column]
        if profile["non_null_count"] == 0 or not profile["id_like"]:
            continue
        score = 0.0
        score += 0.35
        score += min(0.45, float(profile["uniqueness_ratio"]) * 0.45)
        score += min(0.20, float(profile["non_null_ratio"]) * 0.20)
        if profile["distinct_count"] == row_count and row_count > 0:
            score += 0.15
        if score >= 0.72 and profile["uniqueness_ratio"] >= 0.9 and profile["non_null_ratio"] >= 0.8:
            candidates.append(
                {
                    "table": table,
                    "column": column,
                    "confidence": round(min(score, 1.0), 4),
                    "reason": "high uniqueness and identifier-like name",
                }
            )
    candidates.sort(key=lambda item: (item["confidence"], profiles[item["column"]]["id_like"]), reverse=True)
    return candidates[:3]


def _foreign_key_candidates(
    schema: dict[str, list[str]],
    profiles_by_table: dict[str, dict[str, dict[str, Any]]],
    pk_by_table: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for child_table, child_columns in schema.items():
        for child_column in child_columns:
            child_profile = profiles_by_table[child_table][child_column]
            child_values = child_profile.get("_value_set", set())
            if not child_values:
                continue

            for parent_table, pks in pk_by_table.items():
                if parent_table == child_table:
                    continue
                parent_columns = [pk["column"] for pk in pks] or schema.get(parent_table, [])
                for parent_column in parent_columns:
                    parent_profile = profiles_by_table[parent_table][parent_column]
                    parent_values = parent_profile.get("_value_set", set())
                    if not parent_values:
                        continue
                    overlap_count = len(child_values & parent_values)
                    coverage = overlap_count / max(1, len(child_values))
                    parent_coverage = overlap_count / max(1, len(parent_values))
                    name_similarity = _column_name_similarity(child_column, parent_column)
                    id_bonus = 0.15 if child_profile.get("id_like") and parent_profile.get("id_like") else 0.0
                    score = (coverage * 0.65) + (name_similarity * 0.25) + id_bonus

                    if coverage >= 0.8 and score >= 0.72:
                        candidates.append(
                            {
                                "from_table": child_table,
                                "from_column": child_column,
                                "to_table": parent_table,
                                "to_column": parent_column,
                                "confidence": round(min(score, 1.0), 4),
                                "coverage": round(coverage, 4),
                                "parent_coverage": round(parent_coverage, 4),
                                "name_similarity": round(name_similarity, 4),
                            }
                        )

    candidates.sort(key=lambda item: (item["confidence"], item["coverage"], item["name_similarity"]), reverse=True)
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (candidate["from_table"], candidate["from_column"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def infer_relational_schema(db_path: str, schema: dict[str, list[str]]) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        tables: dict[str, Any] = {}
        profiles_by_table: dict[str, dict[str, dict[str, Any]]] = {}
        pk_by_table: dict[str, list[dict[str, Any]]] = {}

        for table, columns in schema.items():
            row_count = _table_row_count(conn, table)
            profiles = {column: _column_profile(conn, table, column, row_count) for column in columns}
            profiles_by_table[table] = profiles
            primary_keys = _primary_key_candidates(table, columns, profiles, row_count)
            pk_by_table[table] = primary_keys
            public_profiles = {
                column: {key: value for key, value in profile.items() if key != "_value_set"}
                for column, profile in profiles.items()
            }
            tables[table] = {
                "row_count": row_count,
                "columns": public_profiles,
                "primary_key_candidates": primary_keys,
            }

        foreign_keys = _foreign_key_candidates(schema, profiles_by_table, pk_by_table) if len(schema) > 1 else []
        return {
            "tables": tables,
            "foreign_key_candidates": foreign_keys,
            "has_relationships": bool(foreign_keys),
        }
    finally:
        conn.close()


def format_relational_schema_for_prompt(relational_schema: dict[str, Any] | None) -> str:
    if not relational_schema:
        return ""

    lines = ["Relational schema hints inferred from uploaded data:"]
    for table, info in relational_schema.get("tables", {}).items():
        lines.append(f'- Table "{table}": {info.get("row_count", 0)} rows')
        for pk in info.get("primary_key_candidates", [])[:2]:
            lines.append(
                f'  - possible primary key: "{pk["column"]}" '
                f'(confidence {pk["confidence"]})'
            )

    fks = relational_schema.get("foreign_key_candidates", [])
    if fks:
        lines.append("Possible relationships:")
        for fk in fks[:10]:
            lines.append(
                f'  - "{fk["from_table"]}"."{fk["from_column"]}" -> '
                f'"{fk["to_table"]}"."{fk["to_column"]}" '
                f'(confidence {fk["confidence"]}, coverage {fk["coverage"]})'
            )
    else:
        lines.append("No reliable foreign-key relationships were inferred. Avoid inventing joins unless the question clearly requires them.")

    return "\n".join(lines)


def format_create_table_schema(schema: dict[str, list[str]], relational_schema: dict[str, Any] | None = None) -> str:
    fk_by_table: dict[str, list[dict[str, Any]]] = {}
    for fk in (relational_schema or {}).get("foreign_key_candidates", []):
        fk_by_table.setdefault(fk["from_table"], []).append(fk)

    statements: list[str] = []
    for table, columns in schema.items():
        table_info = (relational_schema or {}).get("tables", {}).get(table, {})
        pk_cols = [pk["column"] for pk in table_info.get("primary_key_candidates", [])[:1]]
        lines = []
        for column in columns:
            suffix = " PRIMARY KEY" if column in pk_cols else ""
            lines.append(f"  {_quote(column)} TEXT{suffix}")
        for fk in fk_by_table.get(table, [])[:5]:
            lines.append(
                f'  FOREIGN KEY ({_quote(fk["from_column"])}) '
                f'REFERENCES {_quote(fk["to_table"])}({_quote(fk["to_column"])})'
            )
        body = ",\n".join(lines) if lines else "  id TEXT"
        statements.append(f"CREATE TABLE {_quote(table)} (\n{body}\n)")
    return "\n\n".join(statements)
