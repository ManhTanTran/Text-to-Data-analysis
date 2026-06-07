import re

import pandas as pd


SUPPORTED_LANGUAGES = {"vi", "en"}

_AGGREGATE_LABELS = {
    "vi": {
        "AVG": "Trung bình",
        "SUM": "Tổng",
        "COUNT": "Số lượng",
        "MIN": "Nhỏ nhất",
        "MAX": "Lớn nhất",
    },
    "en": {
        "AVG": "Average",
        "SUM": "Total",
        "COUNT": "Count",
        "MIN": "Minimum",
        "MAX": "Maximum",
    },
}

_COLUMN_TRANSLATIONS = {
    "vi": {
        "value": "giá trị",
        "sales value": "giá trị bán hàng",
        "annual sales value": "giá trị bán hàng hằng năm",
        "revenue": "doanh thu",
        "cogs": "chi phí sản xuất",
        "cost of goods sold": "chi phí sản xuất",
        "sales": "doanh số",
        "total sales": "tổng doanh số",
        "price": "giá",
        "quantity": "số lượng",
        "amount": "giá trị",
        "year": "năm",
        "month": "tháng",
        "postcode": "mã bưu chính",
        "sales rep id": "mã nhân viên bán hàng",
        "sales rep name": "tên nhân viên bán hàng",
        "sales representative": "nhân viên bán hàng",
        "representative": "nhân viên bán hàng",
        "average value": "trung bình giá trị",
        "total value": "tổng giá trị",
        "row count": "số dòng",
    },
    "en": {
        "value": "Value",
        "sales value": "Sales Value",
        "annual sales value": "Annual Sales Value",
        "revenue": "Revenue",
        "cogs": "COGS",
        "cost of goods sold": "Cost of Goods Sold",
        "sales": "Sales",
        "total sales": "Total Sales",
        "price": "Price",
        "quantity": "Quantity",
        "amount": "Value",
        "year": "Year",
        "month": "Month",
        "postcode": "Postcode",
        "sales rep id": "Sales Rep ID",
        "sales rep name": "Sales Rep Name",
        "sales representative": "Sales Representative",
        "representative": "Sales Representative",
        "trung bình giá trị": "Average Value",
        "tổng giá trị": "Total Value",
        "số lượng": "Count",
        "số dòng": "Row Count",
        "tên nhân viên bán hàng": "Sales Rep Name",
        "mã nhân viên bán hàng": "Sales Rep ID",
        "giá trị bán hàng": "Sales Value",
        "giá trị": "Value",
    },
}


def normalize_language(language: str | None) -> str:
    lang = (language or "vi").strip().lower()
    return lang if lang in SUPPORTED_LANGUAGES else "vi"


def _capitalize_first(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _title_case(text: str) -> str:
    small_words = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to"}
    words = text.split()
    return " ".join(word.upper() if word.isupper() else (word if i and word.lower() in small_words else word.capitalize()) for i, word in enumerate(words))


def _clean_identifier(identifier: str) -> str:
    text = str(identifier).strip()
    text = text.strip('"').strip("'").strip("`").strip("[]")
    text = re.sub(r"[_\s]+", " ", text).strip()
    return text


def _humanize_identifier(identifier: str, language: str, lower_first: bool = False) -> str:
    lang = normalize_language(language)
    text = _clean_identifier(identifier)
    translated = _COLUMN_TRANSLATIONS[lang].get(text.lower())

    if translated:
        label = translated
    elif lang == "en":
        label = _title_case(text)
    else:
        label = text

    if lower_first:
        return label[:1].lower() + label[1:] if label else label
    return label if lang == "en" else _capitalize_first(label)


def display_column_label(column: str, language: str = "vi") -> str:
    """Convert raw SQL result labels into UI-friendly labels in the selected language."""
    lang = normalize_language(language)
    raw = str(column).strip()
    raw_for_label = re.sub(
        r'\bTO_NUMBER\s*\(\s*("[^"]+"|\'[^\']+\'|`[^`]+`|\[[^\]]+\])\s*\)',
        r"\1",
        raw,
        flags=re.IGNORECASE,
    )
    match = re.match(
        r'^(AVG|SUM|COUNT|MIN|MAX)\s*\(\s*(?:DISTINCT\s+)?(?:"([^"]+)"|'
        r"'([^']+)'|`([^`]+)`|\[([^\]]+)\]|([^)]+))\s*\)$",
        raw_for_label,
        flags=re.IGNORECASE,
    )
    if not match:
        return _humanize_identifier(raw, lang)

    func = match.group(1).upper()
    col = next((part for part in match.groups()[1:] if part), "").strip()
    prefix = _AGGREGATE_LABELS[lang][func]

    if func == "COUNT" and col == "*":
        return prefix

    return f"{prefix} {_humanize_identifier(col, lang, lower_first=True)}"


def humanize_dataframe_columns(df: pd.DataFrame, language: str = "vi") -> pd.DataFrame:
    """Rename DataFrame columns for table headers and chart axis labels."""
    if df is None or df.empty:
        return df

    renamed = df.copy()
    used: dict[str, int] = {}
    columns: list[str] = []
    for col in renamed.columns:
        label = display_column_label(str(col), language)
        count = used.get(label, 0)
        used[label] = count + 1
        columns.append(label if count == 0 else f"{label} ({count + 1})")

    renamed.columns = columns
    return renamed
