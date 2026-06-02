import csv
import io
import json
import re
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd

from modules.numeric import coerce_numeric_columns


# ── Helpers ──────────────────────────────────────────────────────────────────

def safe_table_name(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]", "_", str(name)).strip("_")
    if not s or s[0].isdigit():
        s = "t_" + s
    return s or "sheet"


def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename pandas 'Unnamed: X' placeholders and deduplicate column names."""
    new_cols: list[str] = []
    seen: dict[str, int] = {}
    for i, col in enumerate(df.columns):
        col_str = str(col).strip()
        if re.match(r"Unnamed:\s*\d+", col_str):
            col_str = f"Cột {i + 1}"
        base = col_str
        if base in seen:
            seen[base] += 1
            col_str = f"{base} ({seen[base]})"
        else:
            seen[base] = 0
        new_cols.append(col_str)
    df.columns = new_cols
    return df


def _df_to_sqlite(df: pd.DataFrame, table: str, conn: sqlite3.Connection) -> list[str]:
    df = _clean_columns(df)
    df = coerce_numeric_columns(df)
    df.to_sql(table, conn, if_exists="replace", index=False)
    return list(df.columns)


def _detect_encoding(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            raw.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "latin-1"


def _detect_delimiter(text: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        return ","


# ── Format parsers ────────────────────────────────────────────────────────────

def _parse_excel(raw: bytes) -> tuple[str, dict]:
    xl = pd.ExcelFile(io.BytesIO(raw), engine="openpyxl")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    schema: dict[str, list[str]] = {}
    conn = sqlite3.connect(db_path)
    for sheet in xl.sheet_names:
        df = xl.parse(sheet)
        if df.empty:
            continue
        table = safe_table_name(sheet)
        schema[table] = _df_to_sqlite(df, table, conn)
    conn.close()
    return db_path, schema


def _parse_csv(raw: bytes, filename: str, delimiter: str | None = None) -> tuple[str, dict]:
    enc = _detect_encoding(raw)
    text = raw.decode(enc)
    sep = delimiter or _detect_delimiter(text)
    df = pd.read_csv(io.StringIO(text), sep=sep)

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    table = safe_table_name(Path(filename).stem)
    conn = sqlite3.connect(db_path)
    schema = {table: _df_to_sqlite(df, table, conn)}
    conn.close()
    return db_path, schema


def _parse_json(raw: bytes, filename: str) -> tuple[str, dict]:
    enc = _detect_encoding(raw)
    data = json.loads(raw.decode(enc))

    # Support: list of dicts  OR  {"key": [...]}
    if isinstance(data, list):
        df = pd.json_normalize(data)
    elif isinstance(data, dict):
        # find the first list value
        for v in data.values():
            if isinstance(v, list):
                df = pd.json_normalize(v)
                break
        else:
            df = pd.DataFrame([data])
    else:
        raise ValueError("JSON phải là array hoặc object chứa array")

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    table = safe_table_name(Path(filename).stem)
    conn = sqlite3.connect(db_path)
    schema = {table: _df_to_sqlite(df, table, conn)}
    conn.close()
    return db_path, schema


# ── Public API ────────────────────────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".tsv", ".txt", ".json"}


def parse_file_to_sqlite(raw: bytes, filename: str) -> tuple[str, dict]:
    """Unified file parser. Returns (db_path, schema_dict)."""
    ext = Path(filename).suffix.lower()
    if ext in (".xlsx", ".xls"):
        return _parse_excel(raw)
    elif ext == ".csv":
        return _parse_csv(raw, filename)
    elif ext in (".tsv", ".txt"):
        return _parse_csv(raw, filename, delimiter="\t")
    elif ext == ".json":
        return _parse_json(raw, filename)
    else:
        raise ValueError(f"Định dạng không hỗ trợ: {ext}. Dùng: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
