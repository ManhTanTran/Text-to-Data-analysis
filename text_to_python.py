"""
modules/text_to_python.py
--------------------------
Text-to-Python visualization module.

Method: Zero-Shot Prompting với Local Llama3 qua Ollama API.
Theo paper: "Evaluating Local Open-Source LLMs for Privacy-Preserving Data
Visualization via Zero-Shot Prompting" (Local-Python-Viz).

Pipeline:
    DataFrame + Question
        → Zero-shot Prompt (Context + Requirement + Constraint)
        → Llama3 Local (Ollama)
        → Python matplotlib code
        → Sandbox exec
        → matplotlib.Figure

Privacy advantage: schema/data KHÔNG gửi ra cloud, hoàn toàn xử lý local.
"""

import ast
import io
import re
import traceback
from typing import Optional, Tuple
import os

import matplotlib
matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt
import pandas as pd

from .ollama_client import (
    ollama_complete,
    is_ollama_alive,
    OLLAMA_MODEL,
)


# ── Config ──────────────────────────────────────────────────────────────────
USE_OLLAMA_BY_DEFAULT = True  # Theo paper, ưu tiên Ollama local cho privacy

# Fallback DeepSeek nếu user không có Ollama
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"


# ══════════════════════════════════════════════════════════════════════════
# 1. ZERO-SHOT PROMPT BUILDER (theo Khan et al. 2025 + Local-Python-Viz)
# ══════════════════════════════════════════════════════════════════════════

def build_viz_prompt(question: str, df: pd.DataFrame) -> str:
    """
    Xây dựng Zero-Shot prompt theo cấu trúc 3 phần:

    1. CONTEXT: Tên cột + dtype (Quantitative/Categorical) + sample data
    2. REQUIREMENT: Yêu cầu vẽ biểu đồ
    3. CONSTRAINT: Định dạng output (chỉ Python code, không markdown)
    """
    # Context: column metadata
    col_info = []
    for col in df.columns:
        dtype = df[col].dtype
        kind = "Quantitative" if pd.api.types.is_numeric_dtype(dtype) else "Categorical"
        col_info.append(f"  - {col} ({kind}, dtype={dtype})")
    cols_str = "\n".join(col_info)

    # Sample 3 rows
    sample_data = df.head(3).to_string(index=False)

    prompt = f"""You are a Python data visualization expert.

DataFrame `df` columns:
{cols_str}

Sample data (first 3 rows):
{sample_data}

User request: {question}

Write Python code using matplotlib (and optionally seaborn) to create a clear, well-labeled chart.

Requirements:
- Use the variable `df` which is already loaded as a pandas DataFrame.
- Import only matplotlib.pyplot as plt and seaborn as sns (if needed).
- Always start with: plt.figure(figsize=(10, 6))
- Add title, axis labels, and legend if applicable.
- Use a clean style: plt.style.use('seaborn-v0_8-whitegrid') or 'ggplot'.
- End with plt.tight_layout() — do NOT call plt.show().
- When referring to columns with hyphens or spaces, use bracket notation like df['col-name'].

Output ONLY the Python code — no explanation, no markdown fences."""

    return prompt


# ══════════════════════════════════════════════════════════════════════════
# 2. CODE GENERATION (Ollama local hoặc DeepSeek fallback)
# ══════════════════════════════════════════════════════════════════════════

def generate_viz_code(
    question: str,
    df: pd.DataFrame,
    use_mock: bool = False,
    use_ollama: bool = USE_OLLAMA_BY_DEFAULT,
) -> Tuple[str, str]:
    """
    Sinh mã Python visualization từ câu hỏi + DataFrame.

    Args:
        question: User question
        df: Input DataFrame
        use_mock: Dùng mock template thay vì gọi LLM
        use_ollama: True = Llama3 local, False = DeepSeek API

    Returns:
        (python_code, prompt_used)
    """
    prompt = build_viz_prompt(question, df)

    if use_mock:
        return _mock_viz_code(df, question), prompt

    # Ưu tiên Ollama local theo paper Local-Python-Viz
    if use_ollama:
        if is_ollama_alive():
            try:
                raw = ollama_complete(prompt, model=OLLAMA_MODEL)
                code = _clean_code(raw)
                if _is_valid_code(code):
                    return code, prompt
                # Code không hợp lệ → fallback mock
                return _mock_viz_code(df, question), prompt + "\n\n[Ollama returned invalid code, using mock]"
            except Exception as e:
                # Ollama lỗi → fallback DeepSeek nếu có key, otherwise mock
                err_msg = f"\n\n[Ollama error: {e}]"
                if DEEPSEEK_API_KEY:
                    try:
                        return _generate_viz_deepseek(prompt), prompt + err_msg + " → fallback DeepSeek"
                    except Exception:
                        pass
                return _mock_viz_code(df, question), prompt + err_msg + " → fallback Mock"
        else:
            # Ollama không chạy
            err_msg = "\n\n[Ollama server not running at localhost:11434]"
            if DEEPSEEK_API_KEY:
                try:
                    return _generate_viz_deepseek(prompt), prompt + err_msg + " → using DeepSeek"
                except Exception:
                    pass
            return _mock_viz_code(df, question), prompt + err_msg + " → using Mock"

    # Dùng DeepSeek
    if DEEPSEEK_API_KEY:
        try:
            return _generate_viz_deepseek(prompt), prompt
        except Exception as e:
            return _mock_viz_code(df, question), f"{prompt}\n\n[DeepSeek error: {e}]"

    return _mock_viz_code(df, question), prompt


def _generate_viz_deepseek(prompt: str) -> str:
    """Gọi DeepSeek API để sinh code (fallback)."""
    from openai import OpenAI
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an expert Python data visualization programmer. "
                    "Output ONLY valid Python code, no markdown, no explanation. "
                    "The variable `df` is a pandas DataFrame already in memory."
                )
            },
            {"role": "user", "content": prompt}
        ],
        temperature=0.1,
        max_tokens=800,
    )
    raw = response.choices[0].message.content.strip()
    return _clean_code(raw)


# ══════════════════════════════════════════════════════════════════════════
# 3. CODE CLEANING
# ══════════════════════════════════════════════════════════════════════════

def _clean_code(raw: str) -> str:
    """Clean code từ LLM output: bỏ markdown fences, ANSI escape codes, etc."""
    # Bỏ markdown fences
    m = re.search(r"```(?:python|py)?\s*(.*?)\s*```", raw, re.IGNORECASE | re.DOTALL)
    if m:
        raw = m.group(1).strip()
    else:
        raw = re.sub(r"```python\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"```", "", raw)

    # Bỏ ANSI escape codes
    raw = re.sub(r'\x1b\[[0-9;]*m', '', raw)

    # Bỏ ký tự control khác (giữ \n \t)
    raw = ''.join(c for c in raw if ord(c) >= 32 or c in '\n\r\t')

    return raw.strip()


def _is_valid_code(code: str) -> bool:
    """Quick check: code có syntax valid và có ít nhất 1 plt call?"""
    if not code or len(code) < 20:
        return False
    if "plt." not in code and "sns." not in code:
        return False
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


# ══════════════════════════════════════════════════════════════════════════
# 4. SANDBOX EXECUTOR
# ══════════════════════════════════════════════════════════════════════════

def execute_viz_code(
    code: str,
    df: pd.DataFrame,
) -> Tuple[Optional[plt.Figure], Optional[str]]:
    """
    Thực thi code Python trong sandbox với df có sẵn.
    AST transform: thay Ellipsis (...) bằng `df` để tránh lỗi data=Ellipsis.

    Returns:
        (figure, error_msg)
    """
    plt.close("all")  # dọn figure cũ

    # AST transform để fix data=... thành data=df
    try:
        tree = ast.parse(code)

        class EllipsisToDf(ast.NodeTransformer):
            def visit_Constant(self, node):
                if node.value is Ellipsis:
                    return ast.copy_location(ast.Name(id='df', ctx=ast.Load()), node)
                return node

            # Fallback cho Python cũ
            def visit_Ellipsis(self, node):
                return ast.copy_location(ast.Name(id='df', ctx=ast.Load()), node)

        tree = EllipsisToDf().visit(tree)
        ast.fix_missing_locations(tree)
        compiled = compile(tree, "<viz_code>", "exec")
    except SyntaxError as e:
        return None, f"SyntaxError: {e}\n\nCode:\n{code[:500]}"

    # Sandbox namespace
    namespace = {
        "df": df.copy(),
        "pd": pd,
        "plt": plt,
    }
    try:
        import seaborn as sns
        namespace["sns"] = sns
    except ImportError:
        pass
    try:
        import numpy as np
        namespace["np"] = np
    except ImportError:
        pass

    try:
        exec(compiled, namespace)
        fig = plt.gcf()
        if fig.get_axes():
            return fig, None
        else:
            return None, "Code chạy nhưng không tạo ra biểu đồ nào (no axes)."
    except Exception:
        return None, traceback.format_exc()


# ══════════════════════════════════════════════════════════════════════════
# 5. MOCK CODE GENERATOR
# ══════════════════════════════════════════════════════════════════════════

def _mock_viz_code(df: pd.DataFrame, question: str) -> str:
    """Sinh code matplotlib đơn giản dựa trên cấu trúc DataFrame."""
    num_cols = df.select_dtypes(include="number").columns.tolist()
    cat_cols = df.select_dtypes(exclude="number").columns.tolist()
    q = question.lower()

    # Detect chart type
    if "pie" in q or "tỷ lệ" in q or "proportion" in q:
        chart_type = "pie"
    elif "line" in q or "trend" in q or "tháng" in q or "month" in q or "time" in q:
        chart_type = "line"
    elif "scatter" in q:
        chart_type = "scatter"
    else:
        chart_type = "bar"

    x_col = cat_cols[0] if cat_cols else (df.columns[0] if len(df.columns) > 0 else None)
    y_col = num_cols[0] if num_cols else (df.columns[1] if len(df.columns) > 1 else None)

    if x_col is None or y_col is None:
        return """import matplotlib.pyplot as plt
plt.style.use('ggplot')
fig, ax = plt.subplots(figsize=(8, 4))
ax.text(0.5, 0.5, 'Insufficient data', ha='center', va='center', transform=ax.transAxes)
ax.set_title('No suitable data')
plt.tight_layout()"""

    if chart_type == "bar":
        return f"""import matplotlib.pyplot as plt
plt.style.use('ggplot')
plt.figure(figsize=(10, 6))
ax = plt.gca()
bars = ax.bar(df['{x_col}'].astype(str), df['{y_col}'], color='steelblue', edgecolor='white')
ax.bar_label(bars, fmt='%.0f', padding=3, fontsize=9)
ax.set_xlabel('{x_col}', fontsize=11)
ax.set_ylabel('{y_col}', fontsize=11)
ax.set_title('{y_col} by {x_col}', fontsize=13, fontweight='bold')
plt.xticks(rotation=30, ha='right')
plt.tight_layout()"""

    elif chart_type == "line":
        return f"""import matplotlib.pyplot as plt
plt.style.use('seaborn-v0_8-whitegrid')
plt.figure(figsize=(10, 6))
ax = plt.gca()
ax.plot(df['{x_col}'].astype(str), df['{y_col}'], marker='o', linewidth=2,
        color='#2196F3', markersize=6, markerfacecolor='white', markeredgewidth=2)
ax.fill_between(range(len(df)), df['{y_col}'], alpha=0.1, color='#2196F3')
ax.set_xlabel('{x_col}', fontsize=11)
ax.set_ylabel('{y_col}', fontsize=11)
ax.set_title('{y_col} over {x_col}', fontsize=13, fontweight='bold')
ax.set_xticks(range(len(df)))
ax.set_xticklabels(df['{x_col}'].astype(str), rotation=30, ha='right')
plt.tight_layout()"""

    elif chart_type == "pie":
        return f"""import matplotlib.pyplot as plt
plt.style.use('ggplot')
plt.figure(figsize=(8, 8))
ax = plt.gca()
wedges, texts, autotexts = ax.pie(
    df['{y_col}'],
    labels=df['{x_col}'].astype(str),
    autopct='%1.1f%%',
    startangle=140,
    pctdistance=0.82,
)
for t in autotexts:
    t.set_fontsize(9)
ax.set_title('{y_col} by {x_col}', fontsize=13, fontweight='bold')
plt.tight_layout()"""

    elif chart_type == "scatter" and len(num_cols) >= 2:
        return f"""import matplotlib.pyplot as plt
plt.style.use('seaborn-v0_8-whitegrid')
plt.figure(figsize=(8, 6))
ax = plt.gca()
ax.scatter(df['{num_cols[0]}'], df['{num_cols[1]}'],
           alpha=0.7, s=60, color='coral', edgecolors='white')
ax.set_xlabel('{num_cols[0]}', fontsize=11)
ax.set_ylabel('{num_cols[1]}', fontsize=11)
ax.set_title('{num_cols[0]} vs {num_cols[1]}', fontsize=13, fontweight='bold')
plt.tight_layout()"""

    else:
        return f"""import matplotlib.pyplot as plt
plt.style.use('ggplot')
plt.figure(figsize=(10, 6))
ax = plt.gca()
ax.bar(df['{x_col}'].astype(str), df['{y_col}'], color='steelblue')
ax.set_title('Data Visualization', fontsize=13, fontweight='bold')
plt.tight_layout()"""


# ══════════════════════════════════════════════════════════════════════════
# 6. MAIN PIPELINE
# ══════════════════════════════════════════════════════════════════════════

def text_to_figure(
    question: str,
    df: pd.DataFrame,
    use_mock: bool = False,
    use_ollama: bool = USE_OLLAMA_BY_DEFAULT,
) -> Tuple[Optional[plt.Figure], str, str, Optional[str]]:
    """
    Pipeline hoàn chỉnh: question + DataFrame → matplotlib Figure.

    Returns:
        (figure, viz_code, viz_prompt, error_msg)
        - figure: matplotlib Figure object (or None nếu lỗi)
        - viz_code: Code Python được sinh
        - viz_prompt: Prompt gửi lên LLM
        - error_msg: None nếu thành công, str nếu lỗi
    """
    viz_code, viz_prompt = generate_viz_code(question, df, use_mock=use_mock, use_ollama=use_ollama)
    figure, error = execute_viz_code(viz_code, df)
    return figure, viz_code, viz_prompt, error
