# 🏗️ NL2Viz — System Architecture

## 1. Tổng quan

NL2Viz là một hệ thống pipeline **2-stage LLM** kết hợp:
- **Cloud LLM** (DeepSeek) cho task SQL generation đòi hỏi accuracy cao
- **Local LLM** (Llama3 qua Ollama) cho task code generation đòi hỏi privacy

```
┌────────────────────────────────────────────────────────────┐
│                    NL2Viz Pipeline                         │
└────────────────────────────────────────────────────────────┘

   User Question (NL)
         │
         ▼
   ┌──────────────────┐
   │  Streamlit UI    │  app.py
   │  (input/display) │
   └────────┬─────────┘
            │
            ▼
   ╔══════════════════════════════════════════╗
   ║  STAGE 1 — DAIL-SQL                      ║
   ║  (modules/dail_sql.py)                   ║
   ║                                          ║
   ║  Input:  question + DB schema            ║
   ║  Method: Code Representation Prompt      ║
   ║          + Few-shot Q+SQL pairs          ║
   ║  LLM:    DeepSeek-Chat (cloud API)       ║
   ║  Output: SQL query string                ║
   ╚════════════════╤═════════════════════════╝
                    ▼
   ╔══════════════════════════════════════════╗
   ║  STAGE 2 — SQL Executor                  ║
   ║  (modules/dail_sql.py)                   ║
   ║                                          ║
   ║  Input:  SQL query                       ║
   ║  Method: sqlite3 + pandas                ║
   ║  Output: pandas.DataFrame                ║
   ╚════════════════╤═════════════════════════╝
                    ▼
   ╔══════════════════════════════════════════╗
   ║  STAGE 3 — Local-Python-Viz              ║
   ║  (modules/text_to_python.py)             ║
   ║                                          ║
   ║  Input:  question + DataFrame            ║
   ║  Method: Zero-Shot Prompt                ║
   ║          (Context+Requirement+Constraint)║
   ║  LLM:    Llama3.2 (Ollama local)         ║
   ║  Output: matplotlib code → exec → Figure ║
   ╚════════════════╤═════════════════════════╝
                    ▼
              ┌──────────┐
              │  Chart   │ (PNG inline trong Streamlit)
              └──────────┘
```

## 2. Module Detail

### 2.1. `modules/dail_sql.py` — DAIL-SQL Module

**Trách nhiệm:**
- Đọc DB schema (CREATE TABLE statements)
- Build prompt theo chuẩn DAIL-SQL (Code Representation + Few-shot)
- Gọi DeepSeek API
- Parse SQL output
- Execute SQL → DataFrame

**Key functions:**
- `get_schema(db_path)` — Đọc CREATE TABLE statements
- `build_dail_prompt(question, schema)` — Tạo prompt
- `generate_sql(question, db_path, use_mock)` — Sinh SQL
- `execute_sql(sql, db_path)` — Chạy SQL trên SQLite

### 2.2. `modules/text_to_python.py` — Local-Python-Viz Module

**Trách nhiệm:**
- Build Zero-Shot prompt theo cấu trúc Khan et al. (2025)
- Gọi Ollama local (Llama3.2)
- Clean code (bỏ markdown fences, ANSI codes)
- AST transform (Ellipsis → df)
- Sandbox execute → matplotlib Figure

**Key functions:**
- `build_viz_prompt(question, df)` — Tạo Zero-Shot prompt
- `generate_viz_code(question, df)` — Sinh Python code
- `execute_viz_code(code, df)` — Sandbox exec → Figure
- `text_to_figure(question, df)` — Pipeline tổng hợp

**Fallback chain:**
```
Ollama running? ──Yes──→ Llama3 local ──Success──→ Done
       │                       │
       No                      Failed
       │                       │
       ▼                       ▼
DeepSeek key?  ──Yes──→ DeepSeek API ──Success──→ Done
       │                       │
       No                      Failed
       │                       │
       └──────────────────────┴────→ Mock template
```

### 2.3. `modules/ollama_client.py` — Ollama Wrapper

**Trách nhiệm:**
- Health check (`is_ollama_alive`)
- List local models (`list_local_models`)
- Generate completion (`ollama_complete`)

## 3. Design Decisions

### 3.1. Tại sao tách 2 LLM?

| Stage | Task | Yêu cầu | Lựa chọn |
|---|---|---|---|
| 1 | SQL gen | Accuracy cao, schema phức tạp | Cloud (DeepSeek) |
| 3 | Viz code | Privacy (data nhạy cảm), latency thấp | Local (Llama3) |

**Lý do:**
- **Stage 1** cần LLM mạnh để hiểu schema + sinh SQL chính xác → DeepSeek-Chat (768B model, ~$0.14/M tokens)
- **Stage 3** chỉ cần LLM viết matplotlib code (task quen thuộc) + DataFrame có thể chứa data nhạy cảm → Llama3.2 chạy local

### 3.2. Tại sao Zero-Shot cho Viz code?

Theo paper Local-Python-Viz (Khan et al. 2025):
- Zero-shot đạt 79-95% accuracy với GPT-3.5+
- Llama3 8B cũng đạt ~70% trên các chart phổ biến (Bar, Pie, Line)
- Few-shot không cần thiết vì task này LLM đã quen thuộc

### 3.3. Tại sao Few-shot cho SQL?

Theo paper DAIL-SQL (Gao et al. 2023):
- Zero-shot SQL đạt ~55-65% trên Spider
- Few-shot với DAIL Organization (Q+SQL pairs) đạt 80%+
- Few-shot examples giúp LLM học pattern SQL của domain cụ thể

## 4. Privacy Considerations

| Data | Đi đâu? | Privacy |
|---|---|---|
| User question | DeepSeek API (Stage 1) | Cloud — gửi câu hỏi |
| DB schema | DeepSeek API (Stage 1) | Cloud — gửi schema (nhưng KHÔNG gửi data) |
| DB data (DataFrame) | Llama3 local (Stage 3) | **Local only** — không rời máy |
| Generated SQL/Code | Local | Local |

**Quan trọng:** DataFrame thật (chứa giá trị customer data, revenue numbers, v.v.) chỉ được xử lý local, không gửi lên cloud → đảm bảo privacy cho enterprise data.

## 5. Performance

### Latency (typical)
- Stage 1 (DAIL-SQL): ~1-2s (DeepSeek API)
- Stage 2 (SQL exec): <100ms (SQLite local)
- Stage 3 (Viz): ~3-10s (Llama3 local, GPU acceleration)

**Tổng:** ~5-15s cho 1 query end-to-end.

### Token Usage (per query)
- Stage 1: ~700 tokens (DAIL-SQL có few-shot)
- Stage 3: ~500 tokens (Zero-shot)

## 6. Extension Points

### Thêm chart types
Sửa `_mock_viz_code` trong `text_to_python.py` để add fallback cho chart types mới (radar, heatmap, ...).

### Thêm DB types
Module `dail_sql.py` hiện chỉ support SQLite. Để add MySQL/Postgres:
- Đổi connection string
- Sửa `execute_sql` dùng SQLAlchemy

### Multi-turn conversation
Thêm `chat_history` parameter vào `generate_sql` và include vào prompt.

### Custom few-shot examples
Build skeleton-similarity selector từ training data thay vì few-shot cố định.
