import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any


_BACKEND_DIR = Path(__file__).resolve().parents[1]
_CACHE_DIR = _BACKEND_DIR / "rag_index"
_CACHE_PATH = _CACHE_DIR / "spider_bm25.json"
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u00C0-\u1EF9]+", re.UNICODE)
_INDEX: dict[str, Any] | None = None


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def rag_enabled() -> bool:
    return _env_bool("RAG_ENABLED", True)


def rag_top_k() -> int:
    try:
        return max(0, int(os.getenv("RAG_TOP_K", "4")))
    except ValueError:
        return 4


def spider_data_dir() -> Path:
    configured = os.getenv("SPIDER_DATA_DIR")
    if configured:
        return Path(configured).expanduser()

    candidates = [
        _BACKEND_DIR / ".." / ".." / "LLM to SQL" / "spider_data",
        _BACKEND_DIR / ".." / ".." / ".." / "LLM to SQL" / "spider_data",
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    return candidates[0].resolve()


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(text or "")]


def _schema_text(table_meta: dict[str, Any]) -> str:
    tables = table_meta.get("table_names_original") or table_meta.get("table_names") or []
    columns = table_meta.get("column_names_original") or table_meta.get("column_names") or []

    by_table: dict[int, list[str]] = {}
    for item in columns:
        if not isinstance(item, list) or len(item) < 2:
            continue
        table_idx, column_name = item[0], item[1]
        if table_idx < 0:
            continue
        by_table.setdefault(table_idx, []).append(str(column_name))

    lines: list[str] = []
    for idx, table in enumerate(tables):
        cols = ", ".join(by_table.get(idx, []))
        lines.append(f"{table}: {cols}")
    return "\n".join(lines)


def _features(sql: str) -> dict[str, bool]:
    upper = sql.upper()
    return {
        "has_agg": bool(re.search(r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(", upper)),
        "has_group_by": bool(re.search(r"\bGROUP\s+BY\b", upper)),
        "has_order_by": bool(re.search(r"\bORDER\s+BY\b", upper)),
        "has_join": bool(re.search(r"\bJOIN\b", upper)),
        "has_nested": len(re.findall(r"\bSELECT\b", upper)) > 1,
        "has_limit": bool(re.search(r"\bLIMIT\b", upper)),
    }


def _feature_tokens(text: str) -> list[str]:
    value = text.lower()
    tokens: list[str] = []
    if re.search(r"\b(avg|average|mean)\b|trung\s*b", value):
        tokens += ["avg", "average", "aggregate", "group_by"]
    if re.search(r"\b(sum|total)\b|tổng", value):
        tokens += ["sum", "total", "aggregate", "group_by"]
    if re.search(r"\b(count|number|how many)\b|đếm|số\s*lượng", value):
        tokens += ["count", "aggregate"]
    if re.search(r"\b(top|highest|largest|max|most)\b|cao\s*nhất|lớn\s*nhất", value):
        tokens += ["order_by", "limit", "max"]
    if re.search(r"\b(lowest|smallest|min|least)\b|thấp\s*nhất|nhỏ\s*nhất", value):
        tokens += ["order_by", "limit", "min"]
    if re.search(r"\b(each|by|per|group)\b|theo|mỗi", value):
        tokens += ["group_by"]
    if re.search(r"\b(year|month|date|trend|time)\b|năm|tháng|ngày|xu\s*hướng", value):
        tokens += ["date", "year", "month", "group_by", "order_by"]
    return tokens


def _desired_features(text: str) -> dict[str, bool]:
    value = text.lower()
    return {
        "avg": bool(re.search(r"\b(avg|average|mean)\b|trung\s*b", value)),
        "sum": bool(re.search(r"\b(sum|total)\b|tổng", value)),
        "count": bool(re.search(r"\b(count|number|how many)\b|đếm|số\s*lượng", value)),
        "group_by": bool(re.search(r"\b(each|by|per|group)\b|theo|mỗi", value)),
        "order_limit": bool(re.search(r"\b(top|highest|largest|max|most|lowest|smallest|min|least)\b|cao\s*nhất|lớn\s*nhất|thấp\s*nhất|nhỏ\s*nhất", value)),
        "date": bool(re.search(r"\b(year|month|date|trend|time)\b|năm|tháng|ngày|xu\s*hướng", value)),
    }


def _feature_boost(desired: dict[str, bool], doc: dict[str, Any]) -> float:
    features = doc.get("features", {})
    sql = str(doc.get("query", "")).upper()
    score = 0.0

    if desired.get("avg") and "AVG" in sql:
        score += 8.0
    if desired.get("sum") and "SUM" in sql:
        score += 8.0
    if desired.get("count") and "COUNT" in sql:
        score += 5.0
    if desired.get("group_by") and features.get("has_group_by"):
        score += 8.0
    if desired.get("order_limit") and features.get("has_order_by"):
        score += 5.0
    if desired.get("order_limit") and features.get("has_limit"):
        score += 2.0
    if desired.get("date") and re.search(r"\b(YEAR|MONTH|DATE|strftime)\b", sql, flags=re.IGNORECASE):
        score += 4.0
    if features.get("has_join"):
        score -= 3.0
    if features.get("has_nested"):
        score -= 2.0
    return score


def _doc_text(example: dict[str, Any], schema: str, features: dict[str, bool]) -> str:
    feature_text = " ".join(name for name, enabled in features.items() if enabled)
    return f"{example.get('question', '')}\n{example.get('query', '')}\n{schema}\n{feature_text}"


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_rag_index(force: bool = False) -> dict[str, Any]:
    if _CACHE_PATH.exists() and not force:
        return load_rag_index(allow_build=False)

    base = spider_data_dir()
    train_files = [base / "train_spider.json", base / "train_others.json"]
    tables_path = base / "tables.json"
    if not tables_path.exists() or any(not path.exists() for path in train_files):
        raise FileNotFoundError(f"Spider train files not found in {base}")

    table_meta = {item["db_id"]: item for item in _load_json(tables_path)}
    examples: list[dict[str, Any]] = []
    doc_freq: Counter[str] = Counter()
    total_len = 0

    for train_path in train_files:
        for raw in _load_json(train_path):
            schema = _schema_text(table_meta.get(raw.get("db_id"), {}))
            feats = _features(raw.get("query", ""))
            tokens = _tokenize(_doc_text(raw, schema, feats))
            tokens += _feature_tokens(raw.get("question", ""))
            token_counts = Counter(tokens)
            total_len += len(tokens)
            doc_freq.update(token_counts.keys())
            examples.append(
                {
                    "db_id": raw.get("db_id", ""),
                    "question": raw.get("question", ""),
                    "query": raw.get("query", ""),
                    "schema": schema,
                    "features": feats,
                    "tf": dict(token_counts),
                    "length": len(tokens),
                }
            )

    index = {
        "version": 1,
        "source": str(base),
        "doc_count": len(examples),
        "avgdl": total_len / max(1, len(examples)),
        "df": dict(doc_freq),
        "examples": examples,
    }
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _CACHE_PATH.open("w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False)
    global _INDEX
    _INDEX = index
    return index


def load_rag_index(allow_build: bool = True) -> dict[str, Any]:
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    if _CACHE_PATH.exists():
        with _CACHE_PATH.open("r", encoding="utf-8") as handle:
            _INDEX = json.load(handle)
        return _INDEX
    if allow_build:
        return build_rag_index(force=True)
    raise FileNotFoundError(f"RAG index cache not found: {_CACHE_PATH}")


def rag_status() -> dict[str, Any]:
    count = 0
    ready = False
    if _CACHE_PATH.exists():
        try:
            index = load_rag_index(allow_build=False)
            count = int(index.get("doc_count", 0))
            ready = count > 0
        except Exception:
            ready = False
    return {
        "rag_enabled": rag_enabled(),
        "rag_index_ready": ready,
        "rag_example_count": count,
    }


def _bm25_score(query_tokens: list[str], doc: dict[str, Any], index: dict[str, Any]) -> float:
    k1 = 1.5
    b = 0.75
    n_docs = max(1, int(index.get("doc_count", 0)))
    avgdl = float(index.get("avgdl", 1.0)) or 1.0
    doc_len = max(1, int(doc.get("length", 0)))
    tf = doc.get("tf", {})
    df = index.get("df", {})
    score = 0.0

    for token in set(query_tokens):
        freq = int(tf.get(token, 0))
        if freq <= 0:
            continue
        token_df = int(df.get(token, 0))
        idf = math.log(1 + (n_docs - token_df + 0.5) / (token_df + 0.5))
        denom = freq + k1 * (1 - b + b * doc_len / avgdl)
        score += idf * (freq * (k1 + 1)) / denom
    return score


def _query_text(question: str, schema: dict[str, list[str]]) -> str:
    schema_terms = " ".join([str(table) + " " + " ".join(map(str, cols)) for table, cols in schema.items()])
    return f"{question}\n{schema_terms}"


def retrieve_examples(question: str, schema: dict[str, list[str]], top_k: int | None = None) -> list[dict[str, Any]]:
    if not rag_enabled():
        return []

    limit = rag_top_k() if top_k is None else max(0, top_k)
    if limit <= 0:
        return []

    try:
        index = load_rag_index(allow_build=True)
    except Exception:
        return []

    text = _query_text(question, schema)
    query_tokens = _tokenize(text) + _feature_tokens(text)
    desired = _desired_features(text)
    if not query_tokens:
        return []

    examples = index.get("examples", [])
    prefer_no_join = len(schema) <= 1
    candidates = [
        doc for doc in examples
        if not doc.get("features", {}).get("has_join") and not doc.get("features", {}).get("has_nested")
    ] if prefer_no_join else examples
    if len(candidates) < limit:
        candidates = [doc for doc in examples if not doc.get("features", {}).get("has_join")] if prefer_no_join else examples

    scored: list[tuple[float, dict[str, Any]]] = []
    for doc in candidates:
        score = _bm25_score(query_tokens, doc, index) + _feature_boost(desired, doc)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda item: item[0], reverse=True)

    results: list[dict[str, Any]] = []
    for score, doc in scored[:limit]:
        results.append(
            {
                "db_id": doc.get("db_id", ""),
                "question": doc.get("question", ""),
                "query": doc.get("query", ""),
                "score": round(score, 4),
                "features": doc.get("features", {}),
            }
        )
    return results


def format_examples_for_prompt(examples: list[dict[str, Any]]) -> str:
    if not examples:
        return ""

    lines = [
        "Retrieved Text-to-SQL pattern examples:",
        "Use these only as structural patterns. Do not copy database, table, or column names from them. Map the pattern to the current schema.",
    ]
    for idx, ex in enumerate(examples, start=1):
        lines.append(f"\nExample {idx}:")
        lines.append(f"Question: {ex.get('question', '')}")
        lines.append(f"SQL pattern: {ex.get('query', '')}")
    return "\n".join(lines)
