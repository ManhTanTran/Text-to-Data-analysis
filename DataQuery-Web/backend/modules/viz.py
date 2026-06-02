import base64
import io
import re
import unicodedata
import warnings

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

from modules.numeric import coerce_numeric_columns

warnings.filterwarnings("ignore")

try:
    plt.rcParams["font.family"] = "DejaVu Sans"
    for _font_name in ["Segoe UI", "Arial Unicode MS", "Tahoma", "Arial"]:
        import matplotlib.font_manager as _font_manager

        if any(font.name == _font_name for font in _font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = _font_name
            break
except Exception:
    pass

_BLUE = "#2563eb"
_TEXT = "#111827"
_MUTED = "#6b7280"
_GRID = "#ffffff"
_BORDER = "#e5e7eb"
_PANEL = "#f8fafc"
_IDENTIFIER_TERMS = {"id", "ma", "code", "postcode", "postal", "zip", "phone", "fax"}
_DATE_TERMS = {"year", "nam", "month", "thang", "day", "ngay"}


def _name_key(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _is_year_column(df: pd.DataFrame, column: str) -> bool:
    key = _name_key(column)
    if "year" in key.split() or "nam" in key.split():
        return True

    values = pd.to_numeric(df[column], errors="coerce").dropna()
    if values.empty:
        return False

    integral_ratio = ((values % 1).abs() < 1e-9).mean()
    range_ratio = values.between(1900, 2100).mean()
    return integral_ratio >= 0.95 and range_ratio >= 0.95


def _is_identifier_column(column: str) -> bool:
    return bool(set(_name_key(column).split()) & _IDENTIFIER_TERMS)


def _is_date_like_column(column: str) -> bool:
    return bool(set(_name_key(column).split()) & _DATE_TERMS)


def _is_constant_series(series: pd.Series) -> bool:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return values.empty or values.nunique(dropna=True) <= 1


def _is_chartable_measure(df: pd.DataFrame, column: str) -> bool:
    if _is_year_column(df, column) or _is_date_like_column(column) or _is_identifier_column(column):
        return False
    return not _is_constant_series(df[column])


def _measure_score(column: str) -> int:
    key = _name_key(column)
    score = 0

    if any(term in key for term in ["avg", "average", "mean", "trung binh"]):
        score += 100
    if any(term in key for term in ["sum", "total", "tong"]):
        score += 90
    if any(term in key for term in ["count", "so luong", "row count"]):
        score += 80
    if any(term in key for term in ["value", "gia tri", "revenue", "doanh thu", "sales", "amount", "price", "quantity"]):
        score += 45
    if any(term in key.split() for term in ["id", "ma"]):
        score -= 40
    if any(term in key.split() for term in ["year", "nam", "month", "thang", "day", "ngay"]):
        score -= 100

    return score


def _pick_chart_columns(df: pd.DataFrame, numeric: list[str], text: list[str]) -> tuple[str | None, str | None]:
    if not numeric:
        return None, None

    measure_candidates = [column for column in numeric if _is_chartable_measure(df, column)]
    if not measure_candidates:
        return None, None

    y_col = max(measure_candidates, key=_measure_score)

    if text:
        return text[0], y_col

    x_candidates = [column for column in numeric if column != y_col]
    if not x_candidates:
        return None, y_col

    year_candidates = [column for column in x_candidates if _is_year_column(df, column)]
    if year_candidates:
        return year_candidates[0], y_col

    low_measure_candidates = sorted(x_candidates, key=_measure_score)
    return low_measure_candidates[0], y_col


def _format_value(value: float) -> str:
    if pd.isna(value):
        return ""
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _draw_series(ax, x_labels: list[str], y_values: list[float]) -> None:
    x_pos = list(range(len(y_values)))

    if len(y_values) <= 20:
        ax.bar(x_pos, y_values, color=_BLUE, edgecolor="white", linewidth=0.8, width=0.6)
        max_abs = max([abs(value) for value in y_values] + [1])
        offset = max_abs * 0.015
        for index, value in enumerate(y_values):
            va = "bottom" if value >= 0 else "top"
            y_text = value + offset if value >= 0 else value - offset
            ax.text(index, y_text, _format_value(value), ha="center", va=va, fontsize=7, color="#374151")
        ax.set_xticks(x_pos)
        ax.set_xticklabels(x_labels, rotation=30, ha="right", fontsize=8)
    else:
        ax.plot(x_pos, y_values, color=_BLUE, linewidth=2, marker="o", markersize=3, zorder=3)
        ax.fill_between(x_pos, y_values, alpha=0.12, color=_BLUE)
        step = max(1, len(x_pos) // 10)
        ax.set_xticks(x_pos[::step])
        ax.set_xticklabels(x_labels[::step], rotation=30, ha="right", fontsize=8)


def df_to_chart_base64(df: pd.DataFrame, question: str = "", language: str = "vi") -> tuple[str | None, str | None]:
    if df is None or df.empty:
        return None, "No data"

    df = coerce_numeric_columns(df)
    numeric = df.select_dtypes(include="number").columns.tolist()
    text = df.select_dtypes(exclude="number").columns.tolist()

    if not numeric:
        return None, "No chartable numeric measure"

    if len(df) == 1 and len(numeric) == 1 and not text:
        return None, "single_metric"

    x_col, y_col = _pick_chart_columns(df, numeric, text)
    if not y_col:
        return None, "No chartable numeric measure"

    y_series = pd.to_numeric(df[y_col], errors="coerce")
    valid_y = y_series.dropna()
    if valid_y.empty or valid_y.nunique(dropna=True) <= 1:
        return None, "No chartable numeric measure"

    chart_df = df.loc[y_series.notna()].copy()
    y_values = y_series.loc[y_series.notna()].tolist()
    if x_col:
        x_labels = chart_df[x_col].astype(str).tolist()
        x_axis_label = str(x_col)
    else:
        x_axis_label = "Dòng" if language == "vi" else "Row"
        x_labels = [f"{x_axis_label} {index + 1}" for index in range(len(y_values))]

    fig, ax = plt.subplots(figsize=(10, 4.5))
    fig.patch.set_facecolor("white")
    ax.set_facecolor(_PANEL)
    ax.grid(axis="y", color=_GRID, linewidth=1.2)
    ax.set_axisbelow(True)

    try:
        _draw_series(ax, x_labels, y_values)
        ax.set_xlabel(x_axis_label, fontsize=9, color=_MUTED)
        ax.set_ylabel(str(y_col), fontsize=9, color=_MUTED)
    except Exception as exc:
        plt.close(fig)
        return None, str(exc)

    title = f"{question[:55]}..." if len(question) > 55 else question
    ax.set_title(title, fontsize=11, fontweight="600", pad=12, color=_TEXT)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color(_BORDER)
    ax.tick_params(colors=_MUTED, labelsize=8)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode(), None
