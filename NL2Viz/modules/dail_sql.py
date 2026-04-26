"""
modules/dail_sql.py
-------------------
DAIL-SQL pipeline cho Text-to-SQL conversion.

Method: Code Representation Prompt + DAIL Organization (few-shot Q+SQL pairs)
LLM: DeepSeek-Chat (compatible với OpenAI SDK)

Reference:
    Gao et al., 2023. "Text-to-SQL Empowered by Large Language Models:
    A Benchmark Evaluation"
"""

import os
import re
import sqlite3
from typing import Optional, Tuple
import pandas as pd


# ── Config ──────────────────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL_NAME = "deepseek-chat"


# ── Schema helpers ──────────────────────────────────────────────────────────

def get_schema(db_path: str) -> str:
    """
    Đọc schema từ SQLite DB → trả về CRP (Code Representation Prompt) format.

    Format:
        CREATE TABLE table_name (
          col1 type,  col2 type,
          PRIMARY KEY (col),
          FOREIGN KEY (col) REFERENCES other_table(col)
        )
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]

    schema_parts = []
    for table in tables:
        # Skip sqlite internal tables
        if table.startswith("sqlite_"):
            continue

        cur.execute(f"PRAGMA table_info({table})")
        cols = cur.fetchall()  # (cid, name, type, notnull, dflt, pk)

        cur.execute(f"PRAGMA foreign_key_list({table})")
        fks = cur.fetchall()  # (id, seq, table, from, to, ...)

        col_defs = ", ".join([f"{c[1]} {c[2]}" for c in cols])
        pk_cols = [c[1] for c in cols if c[5]]
        fk_defs = [f"FOREIGN KEY ({f[3]}) REFERENCES {f[2]}({f[4]})" for f in fks]

        all_defs = [col_defs]
        if pk_cols:
            all_defs.append(f"PRIMARY KEY ({', '.join(pk_cols)})")
        all_defs.extend(fk_defs)

        schema_parts.append(f"CREATE TABLE {table} (\n  {',  '.join(all_defs)}\n)")

    conn.close()
    return "\n\n".join(schema_parts)


def get_schema_dict(db_path: str) -> dict:
    """Trả về dict {table_name: [col_names]} để hiển thị trong UI sidebar."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall() if not r[0].startswith("sqlite_")]
    result = {}
    for t in tables:
        cur.execute(f"PRAGMA table_info({t})")
        result[t] = [c[1] for c in cur.fetchall()]
    conn.close()
    return result


def get_sample_rows(db_path: str, table: str, n: int = 3) -> str:
    """Lấy mẫu n rows từ table để bổ sung context (DAIL-SQL approach)."""
    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql_query(f"SELECT * FROM {table} LIMIT {n}", conn)
        conn.close()
        return df.to_string(index=False)
    except Exception:
        return ""


# ── DAIL-SQL Few-shot Examples ───────────────────────────────────────────────

# Few-shot examples cố định cho domain bán hàng (demo DB)
# Trong production thật nên dùng skeleton-similarity selection từ train set
_FEW_SHOT_EXAMPLES = """
/* Answer the following: How many customers are there? */
SELECT COUNT(*) FROM customers

/* Answer the following: What is the total revenue? */
SELECT SUM(revenue) FROM orders

/* Answer the following: List all product categories. */
SELECT DISTINCT category FROM products

/* Answer the following: What is the revenue by region? */
SELECT r.region_name, SUM(o.revenue) AS total_revenue
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
JOIN regions r ON c.region_id = r.region_id
GROUP BY r.region_name
ORDER BY total_revenue DESC

/* Answer the following: Show monthly revenue in 2024. */
SELECT strftime('%m', order_date) AS month, SUM(revenue) AS monthly_revenue
FROM orders
WHERE order_date LIKE '2024%'
GROUP BY month
ORDER BY month

/* Answer the following: Top 5 best-selling products. */
SELECT p.product_name, SUM(o.quantity) AS total_qty
FROM orders o
JOIN products p ON o.product_id = p.product_id
GROUP BY p.product_name
ORDER BY total_qty DESC
LIMIT 5
"""


def build_dail_prompt(question: str, schema: str) -> str:
    """
    Xây dựng prompt theo chuẩn DAIL-SQL:
    1. Few-shot examples (DAIL Organization: Q + SQL pairs)
    2. Database schema (Code Representation: CREATE TABLE statements)
    3. Target question
    """
    prompt = f"""/* Some example questions and corresponding SQL queries based on similar problems: */
{_FEW_SHOT_EXAMPLES}

/* Given the following database schema: */
{schema}

/* Answer the following: {question} */
SELECT"""
    return prompt


# ── SQL Generation ──────────────────────────────────────────────────────────

def generate_sql(
    question: str,
    db_path: str,
    use_mock: bool = False,
) -> Tuple[str, str]:
    """
    Tạo SQL từ câu hỏi tự nhiên.

    Args:
        question: Câu hỏi tự nhiên
        db_path: Đường dẫn SQLite DB
        use_mock: True = dùng mock SQL theo keyword, không gọi LLM

    Returns:
        (sql_query, prompt_used)
    """
    schema = get_schema(db_path)
    prompt = build_dail_prompt(question, schema)

    # Mock mode (không có API key hoặc user chọn mock)
    if use_mock or not DEEPSEEK_API_KEY:
        sql = _mock_sql(question)
        return sql, prompt

    # Real DeepSeek API call
    try:
        from openai import OpenAI
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert SQL assistant. "
                        "Given a database schema and a question, write only the SQL query. "
                        "Output ONLY the SQL query starting with SELECT, no explanation, no markdown."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            max_tokens=512,
        )
        raw = response.choices[0].message.content.strip()
        sql = _extract_sql(raw)
        return sql, prompt

    except Exception as e:
        return f"-- Error calling DeepSeek: {e}", prompt


def _extract_sql(raw: str) -> str:
    """Lấy SQL sạch từ response LLM (xử lý nhiều format)."""
    # Bỏ markdown fences
    raw = re.sub(r"```sql\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"```", "", raw)
    raw = raw.strip()

    # Nếu có ``` ... ``` thì lấy phần trong
    m = re.search(r"```sql\s*(.*?)\s*```", raw, re.IGNORECASE | re.DOTALL)
    if m:
        raw = m.group(1).strip()

    # Đảm bảo bắt đầu bằng SELECT
    if not raw.upper().startswith("SELECT"):
        raw = "SELECT " + raw

    # Bỏ ; cuối nếu có
    raw = raw.rstrip(";").strip()

    return raw


def _mock_sql(question: str) -> str:
    """Mock SQL dựa trên keyword — chỉ dùng khi không có API key."""
    q = question.lower()

    if "month" in q or "tháng" in q:
        return """SELECT strftime('%m', order_date) AS month,
       SUM(revenue) AS monthly_revenue
FROM orders
WHERE order_date LIKE '2024%'
GROUP BY month
ORDER BY month"""

    elif "region" in q or "vùng" in q or "khu vực" in q:
        return """SELECT r.region_name, SUM(o.revenue) AS total_revenue
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
JOIN regions r ON c.region_id = r.region_id
GROUP BY r.region_name
ORDER BY total_revenue DESC"""

    elif "category" in q or "danh mục" in q or "product" in q.lower():
        return """SELECT p.category, SUM(o.revenue) AS total_revenue
FROM orders o
JOIN products p ON o.product_id = p.product_id
GROUP BY p.category
ORDER BY total_revenue DESC"""

    elif "customer" in q or "khách hàng" in q:
        return """SELECT c.customer_name, SUM(o.revenue) AS total_revenue
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
GROUP BY c.customer_name
ORDER BY total_revenue DESC"""

    elif "top" in q or "best" in q:
        return """SELECT p.product_name, SUM(o.quantity) AS total_qty
FROM orders o
JOIN products p ON o.product_id = p.product_id
GROUP BY p.product_name
ORDER BY total_qty DESC
LIMIT 5"""

    else:
        return "SELECT * FROM orders LIMIT 10"


# ── SQL Executor ────────────────────────────────────────────────────────────

def execute_sql(
    sql: str,
    db_path: str,
) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """
    Thực thi SQL trên SQLite database.

    Returns:
        (DataFrame, error_msg)
        - Thành công: (df, None)
        - Lỗi: (None, error_str)
    """
    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql_query(sql, conn)
        conn.close()
        return df, None
    except Exception as e:
        return None, str(e)
