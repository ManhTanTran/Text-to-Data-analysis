import os
import sqlite3
import uuid

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from modules.excel_parser import parse_file_to_sqlite, SUPPORTED_EXTENSIONS
from modules.labels import humanize_dataframe_columns, normalize_language
from modules.nl2sql import (
    DEEPSEEK_MODEL,
    OLLAMA_MODEL,
    generate_default_suggestions,
    generate_mock_sql,
    generate_sql,
    generate_suggestions,
    get_default_provider,
    is_ollama_alive,
)
from modules.numeric import parse_number
from modules.rag import rag_status
from modules.relational_schema import infer_relational_schema
from modules.semantic_schema import infer_semantic_schema
from modules.viz import df_to_chart_base64

app = FastAPI(title="DataQuery AI", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

sessions: dict = {}


@app.get("/api/status")
def status():
    return {
        "has_deepseek_key": bool(os.getenv("DEEPSEEK_API_KEY")),
        "llm_provider": get_default_provider(),
        "deepseek_model": DEEPSEEK_MODEL,
        "ollama_alive": is_ollama_alive(),
        "ollama_model": OLLAMA_MODEL,
        "version": "1.0.0",
    } | rag_status()


@app.get("/api/formats")
def supported_formats():
    return {"extensions": sorted(SUPPORTED_EXTENSIONS)}


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    from pathlib import Path
    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"Định dạng '{ext}' chưa hỗ trợ. Dùng: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")

    content = await file.read()
    try:
        db_path, schema = parse_file_to_sqlite(content, file.filename)
    except Exception as exc:
        raise HTTPException(400, f"Không thể đọc file: {exc}")

    if not schema:
        raise HTTPException(400, "File has no readable sheets")

    semantic_schema = infer_semantic_schema(schema)
    relational_schema = infer_relational_schema(db_path, schema)
    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        "db_path": db_path,
        "schema": schema,
        "semantic_schema": semantic_schema,
        "relational_schema": relational_schema,
        "filename": file.filename,
    }
    return {
        "session_id": session_id,
        "schema": schema,
        "semantic_schema": semantic_schema,
        "relational_schema": relational_schema,
        "filename": file.filename,
    }


@app.get("/api/suggest/{session_id}")
def suggest(session_id: str, language: str = "vi"):
    if session_id not in sessions:
        raise HTTPException(404, "Session not found")
    schema = sessions[session_id]["schema"]
    semantic_schema = sessions[session_id].get("semantic_schema")
    lang = normalize_language(language)
    try:
        suggestions = generate_suggestions(schema, language=lang, semantic_schema=semantic_schema)
    except Exception:
        suggestions = generate_default_suggestions(schema, language=lang)
    return {"suggestions": suggestions}


class QueryBody(BaseModel):
    session_id: str
    question: str
    language: str = "vi"


@app.post("/api/query")
def run_query(body: QueryBody):
    if body.session_id not in sessions:
        raise HTTPException(404, "Session not found")

    sess = sessions[body.session_id]
    llm_provider = get_default_provider()
    lang = normalize_language(body.language)
    rag_used = False
    retrieved_examples = []
    intent = {"name": "unknown"}
    semantic_schema = sess.get("semantic_schema") or infer_semantic_schema(sess["schema"])
    relational_schema = sess.get("relational_schema")
    planner_used = False
    validation_warnings = []
    rag_debug = {}

    llm_error = None
    try:
        (
            sql,
            prompt,
            llm_provider,
            rag_used,
            retrieved_examples,
            intent,
            semantic_schema,
            planner_used,
            validation_warnings,
            rag_debug,
        ) = generate_sql(
            body.question,
            sess["schema"],
            language=lang,
            semantic_schema=semantic_schema,
            relational_schema=relational_schema,
        )
    except Exception as exc:
        llm_error = str(exc)
        llm_provider = "mock"
        try:
            (
                sql,
                prompt,
                llm_provider,
                rag_used,
                retrieved_examples,
                intent,
                semantic_schema,
                planner_used,
                validation_warnings,
                rag_debug,
            ) = generate_sql(
                body.question,
                sess["schema"],
                use_mock=True,
                language=lang,
                semantic_schema=semantic_schema,
                relational_schema=relational_schema,
            )
        except Exception:
            sql = generate_mock_sql(sess["schema"])
            prompt = ""

    df, sql_error = None, None
    conn = None
    try:
        conn = sqlite3.connect(sess["db_path"])
        conn.create_function("TO_NUMBER", 1, parse_number)
        df = pd.read_sql_query(sql, conn)
        conn.close()
        df = humanize_dataframe_columns(df, language=lang)
    except Exception as exc:
        if conn is not None:
            conn.close()
        sql_error = str(exc)

    chart_b64, chart_error = (None, "SQL error") if df is None else df_to_chart_base64(df, body.question, language=lang)

    # single metric value
    single_metric = None
    if df is not None and len(df) == 1 and len(df.columns) == 1:
        single_metric = str(df.iloc[0, 0])

    return {
        "sql": sql,
        "rows": df.to_dict("records") if df is not None else [],
        "columns": list(df.columns) if df is not None else [],
        "chart_base64": chart_b64,
        "sql_error": sql_error,
        "chart_error": chart_error,
        "row_count": len(df) if df is not None else 0,
        "single_metric": single_metric,
        "mock_mode": llm_provider == "mock",
        "llm_provider": llm_provider,
        "llm_error": llm_error,
        "rag_used": rag_used,
        "retrieved_examples": retrieved_examples,
        "llm_prompt": prompt,
        "intent": intent,
        "semantic_schema_used": semantic_schema,
        "relational_schema_used": relational_schema,
        "planner_used": planner_used,
        "validation_warnings": validation_warnings,
        "rag_debug": rag_debug,
        "rag_mode": rag_debug.get("rag_mode"),
        "draft_sql": rag_debug.get("draft_sql"),
        "target_skeleton": rag_debug.get("target_skeleton"),
        "retrieval_stage": rag_debug.get("retrieval_stage"),
    }
