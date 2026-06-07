import json
import math
import os
import re
import importlib.util
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from modules import dail_sql


_BACKEND_DIR = Path(__file__).resolve().parents[1]
_CACHE_DIR = _BACKEND_DIR / "rag_index"
_CACHE_PATH = _CACHE_DIR / "spider_bm25.json"
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[^\W\d_]+", re.UNICODE)
_INDEX: dict[str, Any] | None = None
_EMBED_MODELS: dict[str, Any] = {}

_INDEX_VERSION = 6
_DEFAULT_EMBED_MODEL = "all-MiniLM-L6-v2"
_DAIL_EMBED_MODEL = "sentence-transformers/all-mpnet-base-v2"

_YEAR_MASK_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_NUM_MASK_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_QUOTED_VALUE_RE = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"")
_CAPITALIZED_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")
_STOP_ENTITIES = {
    "What",
    "Which",
    "Who",
    "Whose",
    "Where",
    "When",
    "How",
    "Show",
    "Find",
    "List",
    "Return",
    "Give",
    "Count",
    "For",
    "From",
    "The",
    "Each",
    "All",
    "Are",
    "Is",
}


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


def rag_retrieval_mode() -> str:
    mode = os.getenv("RAG_RETRIEVAL_MODE", "embedding").strip().lower()
    return mode if mode in {"bm25", "embedding", "dail"} else "embedding"


def rag_candidate_pool() -> int:
    try:
        return max(1, int(os.getenv("RAG_CANDIDATE_POOL", "3000")))
    except ValueError:
        return 3000


def dail_two_pass() -> bool:
    return _env_bool("DAIL_TWO_PASS", False)


def rag_cross_domain() -> bool:
    return _env_bool("RAG_CROSS_DOMAIN", False)


def dail_skeleton_threshold() -> float:
    try:
        return float(os.getenv("DAIL_SKELETON_THRESHOLD", "0.85"))
    except ValueError:
        return 0.85


def embed_model_name(mode: str | None = None) -> str:
    configured = os.getenv("EMBED_MODEL")
    if configured:
        return configured.strip()
    return _DAIL_EMBED_MODEL if (mode or rag_retrieval_mode()) == "dail" else _DEFAULT_EMBED_MODEL


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


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", " ", str(value or "").lower()).strip()


def _schema_terms(table_meta: dict[str, Any]) -> set[str]:
    terms: set[str] = set()
    for table in table_meta.get("table_names_original") or table_meta.get("table_names") or []:
        key = _normalize_identifier(table)
        if key:
            terms.add(key)
            terms.update(part for part in key.split() if len(part) > 2)
    for item in table_meta.get("column_names_original") or table_meta.get("column_names") or []:
        if not isinstance(item, list) or len(item) < 2 or item[0] < 0:
            continue
        key = _normalize_identifier(str(item[1]))
        if key:
            terms.add(key)
            terms.update(part for part in key.split() if len(part) > 2)
    return terms


def _mask_schema_terms(text: str, schema_terms: set[str], mask_tag: str) -> str:
    masked = text
    phrases = sorted((term for term in schema_terms if " " in term), key=len, reverse=True)
    for phrase in phrases:
        pattern = re.compile(rf"\b{re.escape(phrase)}\b", flags=re.IGNORECASE)
        masked = pattern.sub(mask_tag, masked)
    words = {term for term in schema_terms if " " not in term and len(term) > 2}
    if words:
        pattern = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")

        def replace_word(match: re.Match) -> str:
            return mask_tag if match.group(0).lower() in words else match.group(0)

        masked = pattern.sub(replace_word, masked)
    return masked


def mask_question(
    text: str,
    table_meta: dict[str, Any] | None = None,
    mask_tag: str = "<mask>",
    value_tag: str = "<unk>",
) -> str:
    item = dail_sql.build_linking_item(text, table_meta or {})
    return dail_sql.mask_question_with_schema_linking([item], mask_tag=mask_tag, value_tag=value_tag)[0]


def _sql_schema_identifiers(table_meta: dict[str, Any]) -> list[str]:
    identifiers: set[str] = set()
    tables = table_meta.get("table_names_original") or table_meta.get("table_names") or []
    columns = table_meta.get("column_names_original") or table_meta.get("column_names") or []
    table_names = [str(table) for table in tables]
    identifiers.update(table_names)
    for item in columns:
        if not isinstance(item, list) or len(item) < 2 or item[0] < 0:
            continue
        table_idx, column_name = int(item[0]), str(item[1])
        identifiers.add(column_name)
        if 0 <= table_idx < len(table_names):
            identifiers.add(f"{table_names[table_idx]}.{column_name}")
    return sorted((identifier for identifier in identifiers if identifier), key=len, reverse=True)


def _space_sql(sql: str) -> str:
    spaced = re.sub(r"([(),=<>+\-*/])", r" \1 ", sql)
    spaced = re.sub(r"\s+", " ", spaced)
    return spaced.strip()


def sql2skeleton(sql: str, table_meta: dict[str, Any] | None = None) -> str:
    return dail_sql.sql2skeleton(sql, table_meta or {})


def jaccard_similarity(skeleton1: str, skeleton2: str) -> float:
    return dail_sql.jaccard_similarity(skeleton1, skeleton2)


def embedding_enabled() -> bool:
    return importlib.util.find_spec("sentence_transformers") is not None


def _embed_cache_path(mode: str | None = None) -> Path:
    model = embed_model_name(mode)
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "_", model).strip("_")
    return _CACHE_DIR / f"embeddings_{slug}.npy"


def _get_embed_model(mode: str | None = None):
    model_name = embed_model_name(mode)
    if model_name not in _EMBED_MODELS:
        from sentence_transformers import SentenceTransformer
        _EMBED_MODELS[model_name] = SentenceTransformer(model_name, device="cpu")
    return _EMBED_MODELS[model_name]


def _embed_query(text: str, mode: str | None = None) -> "Any | None":
    try:
        model = _get_embed_model(mode)
        vec = model.encode([text], normalize_embeddings=True)
        return vec[0].astype("float32")
    except Exception:
        return None


def build_embeddings(index: dict[str, Any], mode: str | None = None) -> "Any | None":
    try:
        import numpy as np
        model = _get_embed_model(mode)
        questions = [ex.get("masked_question") or mask_question(ex.get("question", "")) for ex in index.get("examples", [])]
        if not questions:
            return None
        print(
            f"[RAG] Building embedding cache for {len(questions)} examples "
            f"using '{embed_model_name(mode)}' - this runs once..."
        )
        embeddings = model.encode(
            questions,
            normalize_embeddings=True,
            batch_size=64,
            show_progress_bar=True,
        ).astype("float32")
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        np.save(str(_embed_cache_path(mode)), embeddings)
        print(f"[RAG] Embedding cache saved -> {_embed_cache_path(mode)}")
        return embeddings
    except Exception as exc:
        print(f"[RAG] Embedding build failed ({exc}), falling back to BM25.")
        return None


def load_embeddings(expected_count: int | None = None, mode: str | None = None) -> "Any | None":
    path = _embed_cache_path(mode)
    if not path.exists():
        return None
    try:
        import numpy as np
        emb = np.load(str(path))
        if expected_count is not None and emb.shape[0] != expected_count:
            return None
        return emb
    except Exception:
        return None


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
    if re.search(r"\b(sum|total)\b|tong", value):
        tokens += ["sum", "total", "aggregate", "group_by"]
    if re.search(r"\b(count|number|how many)\b|dem|so\s*luong", value):
        tokens += ["count", "aggregate"]
    if re.search(r"\b(top|highest|largest|max|most)\b|cao\s*nhat|lon\s*nhat", value):
        tokens += ["order_by", "limit", "max"]
    if re.search(r"\b(lowest|smallest|min|least)\b|thap\s*nhat|nho\s*nhat", value):
        tokens += ["order_by", "limit", "min"]
    if re.search(r"\b(each|by|per|group)\b|theo|moi", value):
        tokens += ["group_by"]
    if re.search(r"\b(year|month|date|trend|time)\b|nam|thang|ngay|xu\s*huong", value):
        tokens += ["date", "year", "month", "group_by", "order_by"]
    return tokens


def _desired_features(text: str) -> dict[str, bool]:
    value = text.lower()
    return {
        "avg": bool(re.search(r"\b(avg|average|mean)\b|trung\s*b", value)),
        "sum": bool(re.search(r"\b(sum|total)\b|tong", value)),
        "count": bool(re.search(r"\b(count|number|how many)\b|dem|so\s*luong", value)),
        "group_by": bool(re.search(r"\b(each|by|per|group)\b|theo|moi", value)),
        "order_limit": bool(re.search(r"\b(top|highest|largest|max|most|lowest|smallest|min|least)\b|cao\s*nhat|lon\s*nhat|thap\s*nhat|nho\s*nhat", value)),
        "date": bool(re.search(r"\b(year|month|date|trend|time)\b|nam|thang|ngay|xu\s*huong", value)),
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


def _doc_text(example: dict[str, Any], schema: str, features: dict[str, bool], masked_question: str) -> str:
    feature_text = " ".join(name for name, enabled in features.items() if enabled)
    return f"{masked_question}\n{example.get('query', '')}\n{schema}\n{feature_text}"


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
            meta = table_meta.get(raw.get("db_id"), {})
            schema = _schema_text(meta)
            feats = _features(raw.get("query", ""))
            linking_item = dail_sql.build_linking_item(
                raw.get("question", ""),
                meta,
                query=raw.get("query", ""),
                db_id=raw.get("db_id", ""),
            )
            masked_question = dail_sql.mask_question_with_schema_linking([linking_item])[0]
            question_pattern = dail_sql.get_question_pattern_with_schema_linking([linking_item])[0]
            query_skeleton = sql2skeleton(raw.get("query", ""), meta)
            tokens = _tokenize(_doc_text(raw, schema, feats, masked_question))
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
                    "schema_tokens": sorted(_schema_terms(meta)),
                    "masked_question": masked_question,
                    "question_pattern": question_pattern,
                    "query_skeleton": query_skeleton,
                    "features": feats,
                    "tf": dict(token_counts),
                    "length": len(tokens),
                }
            )

    index = {
        "version": _INDEX_VERSION,
        "source": str(base),
        "doc_count": len(examples),
        "avgdl": total_len / max(1, len(examples)),
        "df": dict(doc_freq),
        "examples": examples,
    }
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _CACHE_PATH.open("w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False)

    for path in _CACHE_DIR.glob("embeddings_*.npy"):
        path.unlink()

    global _INDEX
    _INDEX = index
    return index


def load_rag_index(allow_build: bool = True) -> dict[str, Any]:
    global _INDEX
    if _INDEX is not None:
        if _INDEX.get("version", 1) >= _INDEX_VERSION:
            return _INDEX
        _INDEX = None
    if _CACHE_PATH.exists():
        with _CACHE_PATH.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if loaded.get("version", 1) >= _INDEX_VERSION:
            _INDEX = loaded
            return _INDEX
    if allow_build:
        return build_rag_index(force=True)
    raise FileNotFoundError(f"RAG index cache not found: {_CACHE_PATH}")


def rag_status() -> dict[str, Any]:
    count = 0
    ready = False
    skeleton_ready = False
    if _CACHE_PATH.exists():
        try:
            index = load_rag_index(allow_build=False)
            count = int(index.get("doc_count", 0))
            ready = count > 0
            examples = index.get("examples", [])
            skeleton_ready = bool(examples and examples[0].get("query_skeleton"))
        except Exception:
            ready = False
    mode = rag_retrieval_mode()
    return {
        "rag_enabled": rag_enabled(),
        "rag_index_ready": ready,
        "rag_example_count": count,
        "rag_mode": mode,
        "candidate_pool": rag_candidate_pool(),
        "embed_model": embed_model_name(mode),
        "embedding_enabled": embedding_enabled(),
        "embedding_ready": _embed_cache_path(mode).exists(),
        "dail_two_pass": dail_two_pass(),
        "dail_skeleton_threshold": dail_skeleton_threshold(),
        "skeleton_index_ready": skeleton_ready,
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


def _schema_meta_from_schema(schema: dict[str, list[str]]) -> dict[str, Any]:
    table_names = list(schema.keys())
    column_names: list[list[Any]] = [[-1, "*"]]
    for table_idx, table in enumerate(table_names):
        for col in schema.get(table, []):
            column_names.append([table_idx, col])
    return {"table_names_original": table_names, "column_names_original": column_names}


def _query_text(question: str, schema: dict[str, list[str]]) -> str:
    meta = _schema_meta_from_schema(schema)
    schema_terms = " ".join([str(table) + " " + " ".join(map(str, cols)) for table, cols in schema.items()])
    return f"{mask_question(question, meta)}\n{schema_terms}"


def _candidate_indices(
    examples: list[dict[str, Any]],
    prefer_no_join: bool,
    minimum: int,
    current_db_id: str | None = None,
    cross_domain: bool = False,
) -> list[int]:
    indices = list(range(len(examples)))
    if cross_domain and current_db_id:
        indices = [i for i in indices if examples[i].get("db_id") != current_db_id]
    if prefer_no_join:
        strict = [
            i for i in indices
            if not examples[i].get("features", {}).get("has_join")
            and not examples[i].get("features", {}).get("has_nested")
        ]
        if len(strict) >= minimum:
            return strict
        no_join = [i for i in indices if not examples[i].get("features", {}).get("has_join")]
        if len(no_join) >= minimum:
            return no_join
    return indices


def _format_retrieved_example(score: float, doc: dict[str, Any], mode: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "db_id": doc.get("db_id", ""),
        "question": doc.get("question", ""),
        "query": doc.get("query", ""),
        "score": round(float(score), 4),
        "features": doc.get("features", {}),
        "retrieval_mode": mode,
    }
    if extra:
        payload.update(extra)
    return payload


def retrieve_examples(
    question: str,
    schema: dict[str, list[str]],
    top_k: int | None = None,
    mode: str | None = None,
    target_sql: str | None = None,
    current_db_id: str | None = None,
    cross_domain: bool | None = None,
) -> list[dict[str, Any]]:
    if not rag_enabled():
        return []

    limit = rag_top_k() if top_k is None else max(0, top_k)
    if limit <= 0:
        return []

    selected_mode = mode or rag_retrieval_mode()
    if selected_mode not in {"bm25", "embedding", "dail"}:
        selected_mode = "embedding"

    try:
        index = load_rag_index(allow_build=True)
    except Exception:
        return []

    examples = index.get("examples", [])
    prefer_no_join = len(schema) <= 1
    valid_indices = _candidate_indices(
        examples,
        prefer_no_join=prefer_no_join,
        minimum=limit,
        current_db_id=current_db_id,
        cross_domain=rag_cross_domain() if cross_domain is None else cross_domain,
    )
    candidate_pool_size = min(rag_candidate_pool(), len(valid_indices))
    if candidate_pool_size <= 0:
        return []

    meta = _schema_meta_from_schema(schema)
    masked_question = mask_question(question, meta)
    desired = _desired_features(question)
    target_skeleton = sql2skeleton(target_sql, meta) if target_sql else ""

    if selected_mode in {"embedding", "dail"}:
        embeddings = load_embeddings(expected_count=len(examples), mode=selected_mode)
        if embeddings is None and embedding_enabled():
            embeddings = build_embeddings(index, mode=selected_mode)

        if embeddings is not None:
            try:
                query_emb = _embed_query(masked_question, mode=selected_mode)
                if query_emb is not None:
                    sims = embeddings[valid_indices] @ query_emb
                    top_pool = sorted(
                        zip(sims.tolist(), valid_indices),
                        key=lambda x: x[0],
                        reverse=True,
                    )[:candidate_pool_size]

                    final: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
                    for sim, idx in top_pool:
                        doc = examples[idx]
                        skeleton_score = jaccard_similarity(target_skeleton, doc.get("query_skeleton", "")) if target_skeleton else 0.0
                        feature_score = _feature_boost(desired, doc)
                        score = float(sim) + (0.05 * feature_score)
                        final.append(
                            (
                                score,
                                doc,
                                {
                                    "base_score": round(float(sim), 4),
                                    "skeleton_score": round(float(skeleton_score), 4),
                                },
                            )
                        )

                    if selected_mode == "dail" and target_skeleton:
                        threshold = dail_skeleton_threshold()
                        high = [item for item in final if item[2]["skeleton_score"] >= threshold]
                        low = [item for item in final if item[2]["skeleton_score"] < threshold]
                        high.sort(key=lambda x: (x[2]["skeleton_score"], x[2]["base_score"]), reverse=True)
                        low.sort(key=lambda x: (x[2]["base_score"], x[2]["skeleton_score"]), reverse=True)
                        final = high + low
                    else:
                        final.sort(key=lambda x: x[0], reverse=True)
                    return [_format_retrieved_example(score, doc, selected_mode, extra) for score, doc, extra in final[:limit]]
            except Exception:
                pass

    text = _query_text(question, schema)
    query_tokens = _tokenize(text) + _feature_tokens(text)
    if not query_tokens:
        return []

    stage1: list[tuple[float, dict[str, Any]]] = []
    for idx in valid_indices:
        doc = examples[idx]
        score = _bm25_score(query_tokens, doc, index)
        if score > 0:
            stage1.append((score, doc))
    stage1.sort(key=lambda x: x[0], reverse=True)
    pool = stage1[:candidate_pool_size]

    final_bm25: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for bm25, doc in pool:
        skeleton_score = jaccard_similarity(target_skeleton, doc.get("query_skeleton", "")) if target_skeleton else 0.0
        score = bm25 + _feature_boost(desired, doc)
        final_bm25.append((score, doc, {"base_score": round(float(bm25), 4), "skeleton_score": round(float(skeleton_score), 4)}))
    if selected_mode == "dail" and target_skeleton:
        threshold = dail_skeleton_threshold()
        high = [item for item in final_bm25 if item[2]["skeleton_score"] >= threshold]
        low = [item for item in final_bm25 if item[2]["skeleton_score"] < threshold]
        high.sort(key=lambda x: (x[2]["skeleton_score"], x[2]["base_score"]), reverse=True)
        low.sort(key=lambda x: (x[2]["base_score"], x[2]["skeleton_score"]), reverse=True)
        final_bm25 = high + low
    else:
        final_bm25.sort(key=lambda x: x[0], reverse=True)
    return [_format_retrieved_example(score, doc, "bm25" if selected_mode != "dail" else "dail_bm25", extra) for score, doc, extra in final_bm25[:limit]]


def format_examples_for_prompt(examples: list[dict[str, Any]]) -> str:
    if not examples:
        return ""

    if any(str(ex.get("retrieval_mode", "")).startswith("dail") for ex in examples):
        return dail_sql.format_dail_examples(examples)

    lines = [
        "/* Structural SQL examples - do not copy table or column names; adapt patterns to the current schema only */",
    ]
    for ex in examples:
        lines.append(f"\n-- Question: {ex.get('question', '')}")
        lines.append(ex.get("query", ""))
    return "\n".join(lines)
