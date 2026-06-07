import csv
import io
import json
import re
import sqlite3
import tempfile
import unicodedata
from collections import Counter
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


def _text_key(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _clean_cell(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        value = value.replace("\xa0", " ").strip()
        return value or None
    return value


def _clean_dataframe_values(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.map(_clean_cell) if hasattr(df, "map") else df.applymap(_clean_cell)


def _header_score(values: list[object]) -> int:
    non_empty = [_clean_cell(value) for value in values]
    non_empty = [value for value in non_empty if value is not None]
    if not non_empty:
        return -100

    keys = [_text_key(value) for value in non_empty]
    row_text = " ".join(keys)
    known_headers = {
        "tt",
        "ma sv",
        "ho ten",
        "ngay sinh",
        "so tc dk",
        "tbchk",
        "drl",
        "de xuat hb",
        "ghi chu",
    }
    known_hits = sum(1 for key in keys if key in known_headers)
    text_like = sum(1 for value in non_empty if isinstance(value, str))
    title_penalty = 30 if len(non_empty) <= 2 and any(term in row_text for term in ["bang tong hop", "hoc ky", "nam hoc"]) else 0
    return len(non_empty) * 3 + text_like + known_hits * 8 - title_penalty


def _detect_header_row(raw_df: pd.DataFrame) -> int:
    if raw_df.empty:
        return 0

    best_idx = 0
    best_score = -10_000
    limit = min(len(raw_df), 30)
    for idx in range(limit):
        score = _header_score(list(raw_df.iloc[idx]))
        if score > best_score:
            best_idx = idx
            best_score = score
    return best_idx


def _row_repeats_header(row: pd.Series, columns: list[str]) -> bool:
    matches = 0
    for col, value in zip(columns, row.tolist()):
        if _text_key(value) and _text_key(value) == _text_key(col):
            matches += 1
    return matches >= max(2, len(columns) // 2)


def _parse_excel_sheet(xl: pd.ExcelFile, sheet: str) -> pd.DataFrame:
    raw_df = xl.parse(sheet, header=None)
    raw_df = _clean_dataframe_values(raw_df)
    raw_df = raw_df.dropna(how="all")
    if raw_df.empty:
        return pd.DataFrame()

    header_idx = _detect_header_row(raw_df)
    header_values = [_clean_cell(value) for value in raw_df.iloc[header_idx].tolist()]
    keep_indexes = [idx for idx, value in enumerate(header_values) if value is not None]
    if not keep_indexes:
        return pd.DataFrame()

    columns = [str(header_values[idx]).strip() for idx in keep_indexes]
    df = raw_df.iloc[header_idx + 1:, keep_indexes].copy()
    df.columns = columns
    df = _clean_dataframe_values(df)
    df = df.dropna(how="all")
    if df.empty:
        return df

    repeated_header_mask = df.apply(lambda row: _row_repeats_header(row, columns), axis=1)
    df = df.loc[~repeated_header_mask].reset_index(drop=True)
    return df


def _canonical_columns(df: pd.DataFrame) -> tuple[str, ...]:
    return tuple(_text_key(col) for col in df.columns)


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
    parsed_sheets: list[tuple[str, pd.DataFrame]] = []
    for sheet in xl.sheet_names:
        df = _parse_excel_sheet(xl, sheet)
        if df.empty:
            continue
        parsed_sheets.append((sheet, df))

    if len(parsed_sheets) > 1:
        column_groups = Counter(_canonical_columns(df) for _, df in parsed_sheets)
        dominant_columns, dominant_count = column_groups.most_common(1)[0]
        if dominant_count >= max(2, int(len(parsed_sheets) * 0.6)):
            combined_parts = []
            for sheet, df in parsed_sheets:
                if _canonical_columns(df) != dominant_columns:
                    continue
                part = df.copy()
                part.insert(0, "Sheet", sheet)
                combined_parts.append(part)
            combined = pd.concat(combined_parts, ignore_index=True)
            schema["All_Sheets"] = _df_to_sqlite(combined, "All_Sheets", conn)
        else:
            for sheet, df in parsed_sheets:
                table = safe_table_name(sheet)
                schema[table] = _df_to_sqlite(df, table, conn)
    elif parsed_sheets:
        sheet, df = parsed_sheets[0]
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
