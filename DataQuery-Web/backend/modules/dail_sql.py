import re
from collections import defaultdict
from typing import Any


try:
    from sql_metadata import Parser  # type: ignore
except Exception:  # pragma: no cover - optional dependency fallback
    Parser = None


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "each",
    "for",
    "from",
    "give",
    "how",
    "in",
    "is",
    "list",
    "of",
    "on",
    "or",
    "return",
    "show",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "with",
}
PUNKS = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")

CELL_EXACT_MATCH_FLAG = "EXACTMATCH"
CELL_PARTIAL_MATCH_FLAG = "PARTIALMATCH"
COL_PARTIAL_MATCH_FLAG = "CPM"
COL_EXACT_MATCH_FLAG = "CEM"
TAB_PARTIAL_MATCH_FLAG = "TPM"
TAB_EXACT_MATCH_FLAG = "TEM"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[^\W\d_]+|[^\w\s]", re.UNICODE)
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def tokenize_question(question: str) -> list[str]:
    return [token for token in _TOKEN_RE.findall(question or "") if token.strip()]


def _split_identifier(value: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value or ""))
    text = re.sub(r"[^A-Za-z0-9]+", " ", text)
    return [part.lower() for part in text.split() if part.strip()]


def _schema_token_lists(table_meta: dict[str, Any]) -> tuple[list[list[str]], list[list[str]]]:
    table_names = table_meta.get("table_names_original") or table_meta.get("table_names") or []
    column_names = table_meta.get("column_names_original") or table_meta.get("column_names") or []
    table_tokens = [_split_identifier(str(table)) for table in table_names]
    column_tokens = [["*"]]
    for item in column_names:
        if not isinstance(item, list) or len(item) < 2:
            continue
        if int(item[0]) < 0:
            continue
        column_tokens.append(_split_identifier(str(item[1])))
    return column_tokens, table_tokens


def compute_schema_linking(question_toks: list[str], column: list[list[str]], table: list[list[str]]) -> dict[str, dict[str, str]]:
    """Port of DAIL-SQL/IRNet-style schema linking for local Spider rows."""

    def partial_match(x_list: list[str], y_list: list[str]) -> bool:
        x_str = " ".join(x_list).lower()
        y_str = " ".join(y_list).lower()
        if x_str in STOPWORDS or x_str in PUNKS or not x_str:
            return False
        return bool(re.search(rf"\b{re.escape(x_str)}\b", y_str))

    def exact_match(x_list: list[str], y_list: list[str]) -> bool:
        return " ".join(x_list).lower() == " ".join(y_list).lower()

    q_col_match: dict[str, str] = {}
    q_tab_match: dict[str, str] = {}

    col_id2list = {col_id: col_item for col_id, col_item in enumerate(column) if col_id != 0 and col_item}
    tab_id2list = {tab_id: tab_item for tab_id, tab_item in enumerate(table) if tab_item}
    question = [token.lower() for token in question_toks]

    n = 5
    while n > 0:
        for idx in range(len(question) - n + 1):
            n_gram_list = question[idx:idx + n]
            if not " ".join(n_gram_list).strip():
                continue

            for col_id, col_tokens in col_id2list.items():
                if exact_match(n_gram_list, col_tokens):
                    for q_id in range(idx, idx + n):
                        q_col_match[f"{q_id},{col_id}"] = COL_EXACT_MATCH_FLAG
                elif partial_match(n_gram_list, col_tokens):
                    for q_id in range(idx, idx + n):
                        q_col_match.setdefault(f"{q_id},{col_id}", COL_PARTIAL_MATCH_FLAG)

            for tab_id, tab_tokens in tab_id2list.items():
                if exact_match(n_gram_list, tab_tokens):
                    for q_id in range(idx, idx + n):
                        q_tab_match[f"{q_id},{tab_id}"] = TAB_EXACT_MATCH_FLAG
                elif partial_match(n_gram_list, tab_tokens):
                    for q_id in range(idx, idx + n):
                        q_tab_match.setdefault(f"{q_id},{tab_id}", TAB_PARTIAL_MATCH_FLAG)
        n -= 1

    return {"q_col_match": q_col_match, "q_tab_match": q_tab_match}


def compute_value_linking(question_toks: list[str], table_meta: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Value linking fallback without a DB connection; DAIL uses DB cell lookup when available."""
    column_names = table_meta.get("column_names_original") or table_meta.get("column_names") or []
    column_types = table_meta.get("column_types") or []
    numeric_column_ids = {
        idx
        for idx, item in enumerate(column_names)
        if isinstance(item, list)
        and len(item) >= 2
        and int(item[0]) >= 0
        and idx < len(column_types)
        and str(column_types[idx]).lower() in {"number", "time"}
    }

    num_date_match: dict[str, str] = {}
    cell_match: dict[str, str] = {}
    for q_id, token in enumerate(question_toks):
        key = token.lower()
        if not key or key in STOPWORDS or key in PUNKS:
            continue
        if _NUM_RE.match(key):
            for col_id in numeric_column_ids:
                col_type = str(column_types[col_id]).upper() if col_id < len(column_types) else "NUMBER"
                num_date_match[f"{q_id},{col_id}"] = col_type
        elif token[:1].isupper() and len(token) > 2:
            # Approximate cell value masking for corpora without DB cell-linking annotations.
            for col_id, item in enumerate(column_names):
                if isinstance(item, list) and len(item) >= 2 and int(item[0]) >= 0:
                    cell_match.setdefault(f"{q_id},{col_id}", CELL_PARTIAL_MATCH_FLAG)

    return {"num_date_match": num_date_match, "cell_match": cell_match}


def match_shift(q_col_match: dict[str, str], q_tab_match: dict[str, str], cell_match: dict[str, str]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    q_id_to_match: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for match_key, match_type in q_col_match.items():
        q_id = int(match_key.split(",")[0])
        c_id = int(match_key.split(",")[1])
        q_id_to_match[q_id].append((match_type, c_id))
    for match_key, match_type in q_tab_match.items():
        q_id = int(match_key.split(",")[0])
        t_id = int(match_key.split(",")[1])
        q_id_to_match[q_id].append((match_type, t_id))

    relevant_q_ids = list(q_id_to_match.keys())
    priority: list[tuple[int, int]] = []
    for q_id in q_id_to_match:
        q_id_to_match[q_id] = list(set(q_id_to_match[q_id]))
        priority.append((len(q_id_to_match[q_id]), q_id))
    priority.sort()

    matches: list[tuple[str, int]] = []
    new_q_col_match: dict[str, str] = {}
    new_q_tab_match: dict[str, str] = {}
    for _, q_id in priority:
        overlap = list(set(matches) & set(q_id_to_match[q_id]))
        if not overlap:
            exact_matches = [
                match for match in q_id_to_match[q_id]
                if match[0] in {COL_EXACT_MATCH_FLAG, TAB_EXACT_MATCH_FLAG}
            ]
            selected = exact_matches or q_id_to_match[q_id]
            matches.extend(selected)
        else:
            selected = overlap
        for match_type, c_t_id in selected:
            if match_type in {COL_PARTIAL_MATCH_FLAG, COL_EXACT_MATCH_FLAG}:
                new_q_col_match[f"{q_id},{c_t_id}"] = match_type
            if match_type in {TAB_PARTIAL_MATCH_FLAG, TAB_EXACT_MATCH_FLAG}:
                new_q_tab_match[f"{q_id},{c_t_id}"] = match_type

    new_cell_match: dict[str, str] = {}
    for match_key, match_type in cell_match.items():
        q_id = int(match_key.split(",")[0])
        if q_id in relevant_q_ids:
            continue
        new_cell_match[match_key] = match_type

    return new_q_col_match, new_q_tab_match, new_cell_match


def build_linking_item(question: str, table_meta: dict[str, Any], query: str = "", db_id: str = "") -> dict[str, Any]:
    question_toks = tokenize_question(question)
    column_tokens, table_tokens = _schema_token_lists(table_meta)
    sc_link = compute_schema_linking(question_toks, column_tokens, table_tokens)
    cv_link = compute_value_linking(question_toks, table_meta)
    columns = table_meta.get("column_names_original") or table_meta.get("column_names") or []
    column_to_table = {
        str(idx): int(item[0])
        for idx, item in enumerate(columns)
        if isinstance(item, list) and len(item) >= 2 and int(item[0]) >= 0
    }
    return {
        "db_id": db_id or table_meta.get("db_id", ""),
        "question": question,
        "query": query,
        "question_for_copying": question_toks,
        "sc_link": sc_link,
        "cv_link": cv_link,
        "column_to_table": column_to_table,
        "table_names_original": table_meta.get("table_names_original") or table_meta.get("table_names") or [],
    }


def _mask(question_toks: list[str], mask_ids: list[int], tag: str) -> list[str]:
    mask_set = set(mask_ids)
    return [tag if idx in mask_set else tok for idx, tok in enumerate(question_toks)]


def mask_question_with_schema_linking(data_jsons: list[dict[str, Any]], mask_tag: str = "<mask>", value_tag: str = "<unk>") -> list[str]:
    mask_questions = []
    for data_json in data_jsons:
        sc_link = data_json["sc_link"]
        cv_link = data_json["cv_link"]
        q_col_match = sc_link["q_col_match"]
        q_tab_match = sc_link["q_tab_match"]
        num_date_match = cv_link["num_date_match"]
        cell_match = cv_link["cell_match"]
        question_for_copying = data_json["question_for_copying"]
        q_col_match, q_tab_match, cell_match = match_shift(q_col_match, q_tab_match, cell_match)

        num_date_match_ids = [int(match.split(",")[0]) for match in num_date_match]
        cell_match_ids = [int(match.split(",")[0]) for match in cell_match]
        question_toks = _mask(question_for_copying, num_date_match_ids + cell_match_ids, value_tag)

        q_col_match_ids = [int(match.split(",")[0]) for match in q_col_match]
        q_tab_match_ids = [int(match.split(",")[0]) for match in q_tab_match]
        question_toks = _mask(question_toks, q_col_match_ids + q_tab_match_ids, mask_tag)
        mask_questions.append(" ".join(question_toks))

    return mask_questions


def get_question_pattern_with_schema_linking(data_jsons: list[dict[str, Any]]) -> list[str]:
    question_patterns = []
    for data_json in data_jsons:
        sc_link = data_json["sc_link"]
        cv_link = data_json["cv_link"]
        q_col_match = sc_link["q_col_match"]
        q_tab_match = sc_link["q_tab_match"]
        num_date_match = cv_link["num_date_match"]
        cell_match = cv_link["cell_match"]
        question_for_copying = data_json["question_for_copying"]

        num_date_match_ids = [int(match.split(",")[0]) for match in num_date_match]
        cell_match_ids = [int(match.split(",")[0]) for match in cell_match]
        question_toks = _mask(question_for_copying, num_date_match_ids + cell_match_ids, "_")

        q_col_match_ids = [int(match.split(",")[0]) for match in q_col_match]
        q_tab_match_ids = [int(match.split(",")[0]) for match in q_tab_match]
        question_toks = _mask(question_toks, q_col_match_ids + q_tab_match_ids, "_")
        question_patterns.append(" ".join(question_toks))

    return question_patterns


def _sql_split(sql: str) -> list[str]:
    spaced = re.sub(r"([(),=<>+\-*/])", r" \1 ", sql)
    spaced = re.sub(r"\s+", " ", spaced).strip()
    return spaced.split() if spaced else []


def sql_normalization(sql: str) -> str:
    sql = (sql or "").strip()
    if sql.endswith(";"):
        sql = sql[:-1]
    sql = sql.replace('"', "'")

    if Parser is None:
        return " ".join(_sql_split(sql.lower()))

    def lower_except_quotes(value: str) -> str:
        in_quotation = False
        output = []
        for char in value:
            output.append(char if in_quotation else char.lower())
            if char == "'":
                in_quotation = not in_quotation
        return "".join(output)

    def add_asc(value: str) -> str:
        pattern = re.compile(r"order by (?:\w+ \( \S+ \)|\w+\.\w+|\w+)(?: (?:\+|\-|\<|\<\=|\>|\>\=) (?:\w+ \( \S+ \)|\w+\.\w+|\w+))*")
        if "order by" in value and " asc" not in value and " desc" not in value:
            for found in pattern.findall(value):
                value = value.replace(found, found + " asc")
        return value

    def remove_table_alias(value: str) -> str:
        tables_aliases = Parser(value).tables_aliases
        kept_aliases = {}
        for idx in range(1, 11):
            if f"t{idx}" in tables_aliases:
                kept_aliases[f"t{idx}"] = tables_aliases[f"t{idx}"]
        for tok in _sql_split(value):
            if "." in tok and tok.split(".")[0] in tables_aliases:
                kept_aliases[tok.split(".")[0]] = tables_aliases[tok.split(".")[0]]

        output: list[str] = []
        previous = ""
        for tok in _sql_split(value):
            if tok in kept_aliases:
                if previous == "as":
                    output = output[:-1]
                elif previous != kept_aliases[tok]:
                    output.append(kept_aliases[tok])
            elif "." in tok:
                split_toks = tok.split(".")
                split_toks = [kept_aliases.get(part, part) for part in split_toks]
                output.append(".".join(split_toks))
            else:
                output.append(tok)
            previous = tok

        cleaned: list[str] = []
        for idx, tok in enumerate(output):
            if tok == "as":
                continue
            if idx > 0 and output[idx - 1] == "as":
                continue
            cleaned.append(tok)
        return " ".join(cleaned)

    parsed = Parser(sql)
    whitespace_fixed = " ".join(token.value for token in parsed.tokens)
    return remove_table_alias(add_asc(lower_except_quotes(whitespace_fixed)))


def _schema_identifiers(db_schema: dict[str, Any]) -> tuple[list[str], list[str]]:
    table_names_original: list[str] = []
    table_dot_column_names_original: list[str] = []
    column_names_original = ["*"]
    table_names = db_schema.get("table_names_original") or db_schema.get("table_names") or []
    columns = db_schema.get("column_names_original") or db_schema.get("column_names") or []

    for table_id, table_name_original in enumerate(table_names):
        table_name = str(table_name_original).lower()
        table_names_original.append(table_name)
        table_dot_column_names_original.append(f"{table_name}.*")
        for column_id_and_name in columns:
            if not isinstance(column_id_and_name, list) or len(column_id_and_name) < 2:
                continue
            column_id = int(column_id_and_name[0])
            column_name_original = str(column_id_and_name[1]).lower()
            if column_id == table_id:
                table_dot_column_names_original.append(f"{table_name}.{column_name_original}")
                column_names_original.append(column_name_original)
    return table_names_original, table_dot_column_names_original, column_names_original


def is_negative_int(value: str) -> bool:
    return value.startswith("-") and value[1:].isdigit()


def is_float(value: str) -> bool:
    if value.startswith("-"):
        value = value[1:]
    parts = value.split(".")
    return len(parts) <= 2 and all(part.isdigit() for part in parts)


def sql2skeleton(sql: str, db_schema: dict[str, Any]) -> str:
    sql = sql_normalization(sql)
    table_names, table_dot_column_names, column_names = _schema_identifiers(db_schema)
    tokens = [token.value for token in Parser(sql).tokens] if Parser is not None else _sql_split(sql)

    new_sql_tokens = []
    for token in tokens:
        value = token.strip()
        key = value.lower()
        if key in table_names:
            new_sql_tokens.append("_")
        elif key in column_names or key in table_dot_column_names:
            new_sql_tokens.append("_")
        elif value.startswith("'") and value.endswith("'"):
            new_sql_tokens.append("_")
        elif value.isdigit() or is_negative_int(value) or is_float(value):
            new_sql_tokens.append("_")
        else:
            new_sql_tokens.append(value)

    sql_skeleton = " ".join(new_sql_tokens)
    sql_skeleton = sql_skeleton.replace("on _ = _ and _ = _", "on _ = _")
    sql_skeleton = sql_skeleton.replace("on _ = _ or _ = _", "on _ = _")
    sql_skeleton = sql_skeleton.replace(" on _ = _", "")
    sql_skeleton = re.sub(r"_ (?:join _ ?)+", "_ ", sql_skeleton)

    while "_ , _" in sql_skeleton:
        sql_skeleton = sql_skeleton.replace("_ , _", "_")

    for op in ["=", "!=", ">", ">=", "<", "<="]:
        sql_skeleton = sql_skeleton.replace(f"_ {op} _", "_")
    while "where _ and _" in sql_skeleton or "where _ or _" in sql_skeleton:
        sql_skeleton = sql_skeleton.replace("where _ and _", "where _")
        sql_skeleton = sql_skeleton.replace("where _ or _", "where _")

    while "  " in sql_skeleton:
        sql_skeleton = sql_skeleton.replace("  ", " ")

    split_skeleton = sql_skeleton.split()
    for idx in range(2, len(split_skeleton)):
        if split_skeleton[idx - 2] == "order" and split_skeleton[idx - 1] == "by" and split_skeleton[idx] != "_":
            split_skeleton[idx] = "_"
    return " ".join(split_skeleton)


def jaccard_similarity(skeleton1: str, skeleton2: str) -> float:
    tokens1 = str(skeleton1 or "").strip().split()
    tokens2 = str(skeleton2 or "").strip().split()
    if not tokens1 and not tokens2:
        return 1.0
    if not tokens1 or not tokens2:
        return 0.0
    counts1: dict[str, int] = defaultdict(int)
    counts2: dict[str, int] = defaultdict(int)
    for token in tokens1:
        counts1[token] += 1
    for token in tokens2:
        counts2[token] += 1
    intersection = sum(min(counts1[token], counts2.get(token, 0)) for token in counts1)
    union = len(tokens1) + len(tokens2) - intersection
    return float(intersection) / union if union else 0.0


def format_dail_examples(examples: list[dict[str, Any]]) -> str:
    if not examples:
        return ""
    lines = ["/* Some SQL examples are provided based on similar problems: */"]
    for example in examples:
        lines.append("")
        lines.append(f"/* Answer the following: {example.get('question', '')} */")
        lines.append(str(example.get("query", "")).strip())
    return "\n".join(lines)
