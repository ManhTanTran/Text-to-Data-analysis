import re
import unicodedata
from typing import Any

from modules.labels import display_column_label, normalize_language


def text_key(value: object) -> str:
    text = str(value or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


CONCEPT_ALIASES: dict[str, list[str]] = {
    "time": ["date", "time", "timestamp", "created at", "updated at", "ngay", "thoi gian", "year", "month"],
    "year": ["year", "nam"],
    "revenue": ["revenue", "doanh thu", "sales revenue", "total revenue", "turnover"],
    "cogs": [
        "cogs",
        "cost of goods sold",
        "production cost",
        "manufacturing cost",
        "chi phi san xuat",
        "gia von",
        "gia von hang ban",
        "chi phi hang ban",
        "chi phi von",
    ],
    "profit": ["profit", "gross profit", "net profit", "loi nhuan"],
    "sales_value": ["value", "sales value", "annual sales value", "amount", "gia tri", "gia tri ban hang"],
    "price": ["price", "unit price", "gia", "don gia"],
    "quantity": ["quantity", "qty", "so luong"],
    "sales_rep_id": ["sales rep id", "sales representative id", "rep id", "sales rep", "sales_rep_id"],
    "sales_rep_name": ["sales rep name", "sales representative name", "rep name", "sales_rep_name"],
    "student_id": ["ma sv", "student id", "student code", "mssv"],
    "student_name": ["ho ten", "student name", "name"],
    "score": ["tbchk", "gpa", "score", "grade", "diem", "diem trung binh"],
    "scholarship": ["de xuat hb", "hoc bong", "scholarship", "scholarship proposal"],
    "conduct": ["drl", "conduct", "training score", "ren luyen"],
    "postcode": ["postcode", "postal code", "zip", "ma buu chinh"],
    "sheet": ["sheet", "nganh", "major", "department"],
}


CONCEPT_ROLES: dict[str, list[str]] = {
    "time": ["time", "category"],
    "year": ["time", "category"],
    "revenue": ["measure"],
    "cogs": ["measure"],
    "profit": ["measure"],
    "sales_value": ["measure"],
    "price": ["measure"],
    "quantity": ["measure"],
    "sales_rep_id": ["entity_id", "category"],
    "sales_rep_name": ["entity_name", "category"],
    "student_id": ["entity_id", "category"],
    "student_name": ["entity_name", "category"],
    "score": ["score", "measure"],
    "scholarship": ["status", "category"],
    "conduct": ["status", "category"],
    "postcode": ["category"],
    "sheet": ["category"],
}


def _matches_alias(column_key: str, alias: str) -> bool:
    alias_key = text_key(alias)
    if not alias_key:
        return False
    if column_key == alias_key:
        return True
    if len(alias_key) >= 4 and alias_key in column_key:
        return True
    alias_tokens = set(alias_key.split())
    column_tokens = set(column_key.split())
    return bool(alias_tokens) and alias_tokens.issubset(column_tokens)


def _infer_concepts(column: str) -> list[str]:
    key = text_key(column.replace("_", " "))
    concepts: list[str] = []

    # Specific entity concepts first so broad words like "sales" do not turn IDs into measures.
    ordered_concepts = [
        "sales_rep_id",
        "sales_rep_name",
        "student_id",
        "student_name",
        "scholarship",
        "conduct",
        "score",
        "postcode",
        "sheet",
        "time",
        "year",
        "revenue",
        "cogs",
        "profit",
        "sales_value",
        "price",
        "quantity",
    ]
    for concept in ordered_concepts:
        if any(_matches_alias(key, alias) for alias in CONCEPT_ALIASES[concept]):
            concepts.append(concept)

    if not concepts:
        if "id" in key.split() or key.endswith(" id") or "code" in key:
            concepts.append("generic_id")
        elif "name" in key or "ten" in key:
            concepts.append("generic_name")

    return concepts


def _roles_for_column(column: str, concepts: list[str]) -> list[str]:
    roles: set[str] = set()
    for concept in concepts:
        roles.update(CONCEPT_ROLES.get(concept, []))

    key = text_key(column)
    if not roles:
        if any(term in key for term in ["status", "type", "category", "class", "group"]):
            roles.add("category")
        if any(term in key for term in ["date", "time", "ngay"]):
            roles.update({"time", "category"})
        if any(term in key for term in ["amount", "value", "revenue", "cost", "price"]):
            roles.add("measure")

    if "generic_id" in concepts:
        roles.update({"entity_id", "category"})
    if "generic_name" in concepts:
        roles.update({"entity_name", "category"})

    return sorted(roles)


def _aliases_for_column(column: str, concepts: list[str]) -> list[str]:
    aliases = {column, column.replace("_", " "), text_key(column)}
    for concept in concepts:
        aliases.update(CONCEPT_ALIASES.get(concept, []))
    for lang in ("vi", "en"):
        aliases.add(display_column_label(column, lang))
    return sorted(alias for alias in aliases if text_key(alias))


def infer_semantic_schema(schema: dict[str, list[str]]) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    lookup: dict[str, dict[str, str]] = {}

    for table, columns in schema.items():
        table_info: dict[str, Any] = {
            "columns": {},
            "role_columns": {},
            "concept_columns": {},
        }
        for column in columns:
            concepts = _infer_concepts(str(column))
            roles = _roles_for_column(str(column), concepts)
            aliases = _aliases_for_column(str(column), concepts)
            info = {
                "roles": roles,
                "concepts": concepts,
                "aliases": aliases,
            }
            table_info["columns"][column] = info
            for role in roles:
                table_info["role_columns"].setdefault(role, []).append(column)
            for concept in concepts:
                table_info["concept_columns"].setdefault(concept, column)
            for alias in aliases:
                key = text_key(alias)
                if key:
                    lookup[key] = {"table": table, "column": column}

        tables[table] = table_info

    return {"tables": tables, "lookup": lookup}


def find_columns_by_role(semantic_schema: dict[str, Any], table: str, role: str) -> list[str]:
    return list(semantic_schema.get("tables", {}).get(table, {}).get("role_columns", {}).get(role, []))


def find_column_by_concept(semantic_schema: dict[str, Any], table: str, *concepts: str) -> str | None:
    concept_columns = semantic_schema.get("tables", {}).get(table, {}).get("concept_columns", {})
    for concept in concepts:
        found = concept_columns.get(concept)
        if found:
            return found
    return None


def find_column_by_terms(semantic_schema: dict[str, Any], table: str, terms: list[str]) -> str | None:
    table_columns = semantic_schema.get("tables", {}).get(table, {}).get("columns", {})
    term_keys = [text_key(term) for term in terms if text_key(term)]
    for column, info in table_columns.items():
        aliases = info.get("aliases", [])
        alias_keys = {text_key(alias) for alias in aliases}
        column_key = text_key(column)
        for term_key in term_keys:
            if term_key in alias_keys or term_key == column_key:
                return column
            if len(term_key) >= 4 and (term_key in column_key or any(term_key in alias_key for alias_key in alias_keys)):
                return column
    return None


def format_semantic_schema_for_prompt(semantic_schema: dict[str, Any]) -> str:
    lines = ["Semantic schema:"]
    for table, table_info in semantic_schema.get("tables", {}).items():
        lines.append(f'Table "{table}":')
        for column, info in table_info.get("columns", {}).items():
            roles = ", ".join(info.get("roles", [])) or "unknown"
            concepts = ", ".join(info.get("concepts", [])) or "generic"
            lines.append(f'  - "{column}": roles={roles}; concepts={concepts}')
    return "\n".join(lines)


def semantic_suggestions(schema: dict[str, list[str]], semantic_schema: dict[str, Any] | None = None, language: str = "vi") -> list[str]:
    lang = normalize_language(language)
    semantic_schema = semantic_schema or infer_semantic_schema(schema)
    suggestions: list[str] = []

    for table in schema:
        time_col = find_column_by_concept(semantic_schema, table, "time", "year")
        revenue_col = find_column_by_concept(semantic_schema, table, "revenue")
        cogs_col = find_column_by_concept(semantic_schema, table, "cogs")
        score_col = find_column_by_concept(semantic_schema, table, "score")
        sheet_col = find_column_by_concept(semantic_schema, table, "sheet")
        scholarship_col = find_column_by_concept(semantic_schema, table, "scholarship")
        conduct_col = find_column_by_concept(semantic_schema, table, "conduct")
        student_id_col = find_column_by_concept(semantic_schema, table, "student_id")
        student_name_col = find_column_by_concept(semantic_schema, table, "student_name")
        sales_rep_id_col = find_column_by_concept(semantic_schema, table, "sales_rep_id")
        sales_rep_name_col = find_column_by_concept(semantic_schema, table, "sales_rep_name")
        value_col = find_column_by_concept(semantic_schema, table, "sales_value", "revenue")
        postcode_col = find_column_by_concept(semantic_schema, table, "postcode")

        if lang == "vi":
            if time_col and (revenue_col or cogs_col):
                if revenue_col and cogs_col:
                    suggestions.append("Tổng doanh thu và tổng chi phí sản xuất có tăng giảm theo thời gian không?")
                    suggestions.append("Tổng doanh thu và tổng chi phí sản xuất là bao nhiêu?")
                elif revenue_col:
                    suggestions.append("Tổng doanh thu có tăng giảm theo thời gian không?")
            if score_col and (student_id_col or student_name_col):
                suggestions.append("Có bao nhiêu sinh viên trong dữ liệu?")
                suggestions.append(f"Top 10 sinh viên có {score_col} cao nhất là ai?")
            if score_col and sheet_col:
                suggestions.append(f"Điểm {score_col} trung bình theo từng {sheet_col} là bao nhiêu?")
            if scholarship_col:
                suggestions.append("Sinh viên nào được đề xuất học bổng Xuất sắc?")
                suggestions.append(f"Có bao nhiêu sinh viên theo từng mức {scholarship_col}?")
            if conduct_col:
                suggestions.append(f"Phân bố {conduct_col} theo từng mức là như thế nào?")
            if value_col and (sales_rep_id_col or sales_rep_name_col):
                suggestions.append("Hiện top 10 nhân viên có doanh thu cao nhất")
                suggestions.append("Có những nhân viên bán hàng nào có hiệu suất kinh doanh vượt trội so với bình quân?")
            if value_col and postcode_col:
                suggestions.append(f"{postcode_col} nào có tổng doanh thu cao nhất?")
        else:
            if time_col and (revenue_col or cogs_col):
                if revenue_col and cogs_col:
                    suggestions.append("Do total revenue and production cost increase or decrease over time?")
                    suggestions.append("What are total revenue and total production cost?")
                elif revenue_col:
                    suggestions.append("Does total revenue change over time?")
            if score_col and (student_id_col or student_name_col):
                suggestions.append("How many students are in the data?")
                suggestions.append(f"Show the top 10 students by {score_col}")
            if score_col and sheet_col:
                suggestions.append(f"What is the average {score_col} for each {sheet_col}?")
            if scholarship_col:
                suggestions.append("Which students are proposed for Excellent scholarships?")
                suggestions.append(f"How many students are in each {scholarship_col} level?")
            if conduct_col:
                suggestions.append(f"What is the distribution of {conduct_col} levels?")
            if value_col and (sales_rep_id_col or sales_rep_name_col):
                suggestions.append("Show the top 10 sales representatives by revenue")
                suggestions.append("Which sales representatives are above the average sales performance?")
            if value_col and postcode_col:
                suggestions.append(f"Which {postcode_col} has the highest total revenue?")

    deduped: list[str] = []
    seen: set[str] = set()
    for suggestion in suggestions:
        key = text_key(suggestion)
        if key and key not in seen:
            deduped.append(suggestion)
            seen.add(key)
        if len(deduped) >= 6:
            break
    return deduped
