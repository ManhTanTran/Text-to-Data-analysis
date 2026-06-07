import re
import unicodedata
from typing import Any

import pandas as pd


_NULL_LITERALS = {"", "nan", "none", "null", "n/a", "na", "-"}
_IDENTIFIER_TERMS = {"id", "code", "postcode", "postal", "zip", "phone", "fax", "date", "birth", "dob", "ngay"}


def _name_key(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def parse_number(value: Any) -> float | None:
    """Parse numbers stored as text, including comma thousands and currency symbols."""
    if value is None:
        return None

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if pd.isna(value):
            return None
        return float(value)

    text = str(value).strip()
    if text.lower() in _NULL_LITERALS:
        return None

    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]

    text = re.sub(r"[\s$€£¥₫%]", "", text)
    text = re.sub(r"[^0-9,.\-]", "", text)

    if not text or not re.search(r"\d", text):
        return None

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts) > 1 and len(parts[-1]) == 3 and all(len(part) == 3 for part in parts[1:]):
            text = "".join(parts)
        elif len(parts) == 2 and len(parts[-1]) in {1, 2}:
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "." in text:
        parts = text.split(".")
        if len(parts) > 1 and len(parts[-1]) == 3 and all(len(part) == 3 for part in parts[1:]):
            text = "".join(parts)

    try:
        number = float(text)
    except ValueError:
        return None

    return -number if negative else number


def coerce_numeric_columns(df: pd.DataFrame, threshold: float = 0.65) -> pd.DataFrame:
    """Convert object columns to numeric when most non-empty values parse as numbers."""
    if df is None or df.empty:
        return df

    converted_df = df.copy()
    for col in converted_df.select_dtypes(include="object").columns:
        if set(_name_key(col).split()) & _IDENTIFIER_TERMS:
            continue

        original = converted_df[col]
        non_empty = original.dropna().astype(str).str.strip()
        non_empty = non_empty[~non_empty.str.lower().isin(_NULL_LITERALS)]
        if non_empty.empty:
            continue

        parsed = original.map(parse_number)
        ratio = parsed.notna().sum() / max(len(non_empty), 1)
        if ratio >= threshold:
            converted_df[col] = parsed

    return converted_df
