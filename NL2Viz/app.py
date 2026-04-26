"""
app.py — NL2Viz Streamlit App
==============================

Pipeline:
    User Question
        → DAIL-SQL (DeepSeek API)        → SQL Query
        → SQLite Execute                  → DataFrame
        → Text-to-Python (Llama3 local)  → matplotlib Figure

Run:
    streamlit run app.py

Configure API keys:
    Windows:  $env:DEEPSEEK_API_KEY = "sk-..."
    Linux:    export DEEPSEEK_API_KEY="sk-..."

Make sure Ollama is running:
    ollama serve
    ollama pull llama3.2
"""

import os
import sys
import streamlit as st
import pandas as pd

# Add modules to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.dail_sql import (
    generate_sql, execute_sql,
    get_schema_dict, get_schema,
)
from modules.text_to_python import text_to_figure
from modules.ollama_client import is_ollama_alive, list_local_models, OLLAMA_MODEL

# ── Config ──────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_db", "sales_demo.db")
HAS_DEEPSEEK_KEY = bool(os.getenv("DEEPSEEK_API_KEY", ""))
OLLAMA_RUNNING = is_ollama_alive()

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="NL2Viz — Text to SQL to Chart",
    page_icon="📊",
    layout="wide",
)

# ── Custom CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main { max-width: 1200px; }
    .stCodeBlock { font-size: 13px; }
    .badge-mock {
        background:#FFF3CD; color:#856404;
        padding:4px 10px; border-radius:12px;
        font-size:12px; font-weight:600;
    }
    .badge-real {
        background:#D1ECF1; color:#0C5460;
        padding:4px 10px; border-radius:12px;
        font-size:12px; font-weight:600;
    }
    .badge-ollama {
        background:#D4EDDA; color:#155724;
        padding:4px 10px; border-radius:12px;
        font-size:12px; font-weight:600;
    }
    .step-label {
        background:#F0F2F6; border-left:3px solid #4C7EFF;
        padding:6px 12px; border-radius:4px;
        font-weight:600; font-size:14px; margin:8px 0;
    }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Cấu hình")

    # Status: DAIL-SQL (DeepSeek)
    st.subheader("🔮 Stage 1: DAIL-SQL")
    if HAS_DEEPSEEK_KEY:
        st.markdown('<span class="badge-real">🟢 DeepSeek API connected</span>', unsafe_allow_html=True)
        sql_use_mock = st.checkbox("Use mock SQL (debugging)", value=False)
    else:
        st.markdown('<span class="badge-mock">🟡 No DeepSeek key — mock mode</span>', unsafe_allow_html=True)
        st.caption("Set DEEPSEEK_API_KEY env var to enable real DAIL-SQL.")
        sql_use_mock = True

    st.divider()

    # Status: Text-to-Python (Ollama)
    st.subheader("🦙 Stage 3: Text-to-Python")
    if OLLAMA_RUNNING:
        local_models = list_local_models()
        st.markdown('<span class="badge-ollama">🟢 Ollama running</span>', unsafe_allow_html=True)
        if local_models:
            selected_model = st.selectbox(
                "Llama model:",
                options=local_models,
                index=local_models.index(OLLAMA_MODEL) if OLLAMA_MODEL in local_models else 0
            )
            os.environ["OLLAMA_MODEL_OVERRIDE"] = selected_model
        else:
            st.warning("No models found. Run: `ollama pull llama3.2`")
        viz_use_ollama = st.checkbox("Use Ollama local (privacy mode)", value=True)
    else:
        st.markdown('<span class="badge-mock">🟡 Ollama not running</span>', unsafe_allow_html=True)
        st.caption("Start: `ollama serve` (terminal khác)")
        viz_use_ollama = False
        if HAS_DEEPSEEK_KEY:
            st.info("Sẽ dùng DeepSeek làm fallback")

    viz_use_mock = st.checkbox("Use mock Viz code (debugging)", value=False)

    st.divider()

    # DB schema
    st.subheader("🗄️ Database Schema")
    if not os.path.exists(DB_PATH):
        st.error(f"Demo DB chưa tạo. Chạy:\n```\npython scripts/create_demo_db.py\n```")
        st.stop()

    try:
        schema_dict = get_schema_dict(DB_PATH)
        for table, cols in schema_dict.items():
            with st.expander(f"📋 {table} ({len(cols)} cols)"):
                for c in cols:
                    st.caption(f"• {c}")
    except Exception as e:
        st.error(f"Cannot read DB: {e}")
        st.stop()

    st.divider()
    st.subheader("💡 Gợi ý câu hỏi")
    suggestions = [
        "Show monthly revenue in 2024",
        "Revenue by product category",
        "Revenue by region",
        "Top 5 best-selling products",
        "Revenue by customer",
    ]
    for s in suggestions:
        if st.button(s, key=f"sug_{s}", use_container_width=True):
            st.session_state["question_input"] = s

# ── Main area ────────────────────────────────────────────────────────────────
st.title("📊 NL2Viz — Natural Language to Visualization")
st.caption(
    "Pipeline: Câu hỏi → **DAIL-SQL** (DeepSeek) → SQL → DataFrame → "
    "**Text-to-Python** (Ollama Llama3 local) → Biểu đồ"
)

# Input
question = st.text_input(
    "Nhập câu hỏi của bạn:",
    value=st.session_state.get("question_input", "Show monthly revenue in 2024"),
    placeholder="Ví dụ: What is the revenue by region?",
    key="question_input",
)

col_run, col_chart_type = st.columns([3, 1])
with col_run:
    run_btn = st.button("▶ Chạy Pipeline", type="primary", use_container_width=True)
with col_chart_type:
    chart_hint = st.selectbox(
        "Gợi ý chart",
        ["auto", "bar chart", "line chart", "pie chart", "scatter plot"],
        index=0,
        label_visibility="collapsed",
    )

# ── Pipeline execution ──────────────────────────────────────────────────────
if run_btn and question:
    full_question = question
    if chart_hint != "auto":
        full_question = f"{question} (as a {chart_hint})"

    # ═══ STAGE 1: DAIL-SQL → SQL ═══
    st.markdown(
        '<div class="step-label">Stage 1 · DAIL-SQL → SQL Query</div>',
        unsafe_allow_html=True
    )

    with st.spinner("🔄 Đang sinh SQL..."):
        sql_query, sql_prompt = generate_sql(question, DB_PATH, use_mock=sql_use_mock)

    col1, col2 = st.columns([1, 1])
    with col1:
        st.caption("**SQL được sinh ra:**")
        st.code(sql_query, language="sql")
    with col2:
        with st.expander("📄 Xem DAIL Prompt"):
            st.code(sql_prompt, language="text")

    # ═══ STAGE 2: Execute SQL → DataFrame ═══
    st.markdown(
        '<div class="step-label">Stage 2 · Thực thi SQL → DataFrame</div>',
        unsafe_allow_html=True
    )

    df, sql_error = execute_sql(sql_query, DB_PATH)

    if sql_error:
        st.error(f"❌ Lỗi SQL: {sql_error}")
        st.stop()

    if df is None or df.empty:
        st.warning("⚠️ Query thành công nhưng không có dữ liệu trả về.")
        st.stop()

    st.success(f"✅ Trả về {len(df)} dòng × {len(df.columns)} cột")
    st.dataframe(df, use_container_width=True, height=200)

    # ═══ STAGE 3: Text-to-Python → Chart ═══
    st.markdown(
        '<div class="step-label">Stage 3 · Text-to-Python → Biểu đồ</div>',
        unsafe_allow_html=True
    )

    with st.spinner("🎨 Đang sinh code Python và vẽ biểu đồ..."):
        figure, viz_code, viz_prompt, viz_error = text_to_figure(
            full_question, df, use_mock=viz_use_mock, use_ollama=viz_use_ollama
        )

    tab_chart, tab_code, tab_prompt = st.tabs(["📊 Biểu đồ", "🐍 Python Code", "📄 Viz Prompt"])

    with tab_chart:
        if viz_error:
            st.error(f"❌ Lỗi khi vẽ biểu đồ:\n```\n{viz_error}\n```")
        elif figure:
            st.pyplot(figure, use_container_width=True)
            st.success("✅ Biểu đồ tạo thành công!")
        else:
            st.warning("Không tạo được biểu đồ.")

    with tab_code:
        st.caption("Mã Python được sinh bởi LLM:")
        st.code(viz_code, language="python")

    with tab_prompt:
        st.caption("Prompt gửi cho Text-to-Python module:")
        st.code(viz_prompt, language="text")

    # Summary
    st.divider()
    m1, m2, m3 = st.columns(3)
    m1.metric("Rows returned", len(df))
    m2.metric("Columns", len(df.columns))
    m3.metric(
        "Mode",
        "Llama3 Local" if (viz_use_ollama and OLLAMA_RUNNING and not viz_use_mock) else
        ("DeepSeek" if (HAS_DEEPSEEK_KEY and not viz_use_mock) else "Mock"),
    )

elif not run_btn:
    st.info("👈 Nhập câu hỏi hoặc chọn gợi ý từ sidebar, rồi nhấn **Chạy Pipeline**.")

    with st.expander("📖 Architecture overview"):
        st.markdown("""
**Stage 1 — DAIL-SQL (DeepSeek API)**
- Code Representation Prompt (CRP)
- Few-shot examples với Q+SQL pairs
- Output: SQL query

**Stage 2 — SQLite Executor**
- Run SQL trên local DB
- Return DataFrame

**Stage 3 — Local-Python-Viz (Ollama Llama3)**
- Zero-shot prompt (Context + Requirement + Constraint)
- Llama3 chạy local → privacy-preserving
- Sinh matplotlib code → sandbox exec → Figure
        """)

    with st.expander("🦙 Hướng dẫn cài Ollama"):
        st.markdown("""
1. **Tải Ollama:** https://ollama.com/download

2. **Pull model:**
```bash
ollama pull llama3.2
```

3. **Start server (giữ terminal chạy):**
```bash
ollama serve
```

4. **Verify:**
```bash
curl http://localhost:11434/api/tags
```

5. **Restart streamlit app** để app detect được Ollama.
        """)
