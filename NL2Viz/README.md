# 🚀 NL2Viz

**Natural Language → SQL → Visualization**  
Đặt câu hỏi bằng ngôn ngữ tự nhiên về database của bạn — nhận lại biểu đồ ngay tức thì.

---

## 📋 Requirements

| Dependency | Version | Mục đích |
|---|---|---|
| Python | 3.10+ | Runtime |
| Streamlit | ≥ 1.32 | Web UI |
| Ollama | latest | Chạy Llama3 local |
| DeepSeek API key | — | Stage 1: SQL generation |
| pandas | ≥ 2.0 | DataFrame xử lý |
| matplotlib | ≥ 3.8 | Render chart |

---

## 🛠️ Installation

### Bước 1 — Cài Python dependencies

```bash
cd NL2Viz
pip install -r requirements.txt
```

### Bước 2 — Cài & khởi động Ollama

```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh

# Pull model Llama3.2
ollama pull llama3.2

# Khởi động service (nếu chưa chạy)
ollama serve
```

> **Windows:** Tải installer tại [ollama.com/download](https://ollama.com/download)

### Bước 3 — Cấu hình API key

Tạo file `.env` trong thư mục `NL2Viz/`:

```env
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
```

Hoặc export environment variable:

```bash
export DEEPSEEK_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxx"
```

---

## ▶️ Chạy App

```bash
streamlit run app.py
```

Truy cập `http://localhost:8501`

---

## 🧪 Usage

1. **Upload SQLite database** (`.db` file) hoặc dùng sample data đi kèm trong `data/`
2. **Gõ câu hỏi** bằng tiếng Việt hoặc tiếng Anh, ví dụ:
   - *"Top 5 sản phẩm bán chạy nhất tháng 12?"*
   - *"Doanh thu theo từng khu vực trong Q3?"*
   - *"So sánh số lượng đơn hàng giữa các năm"*
3. **Nhận kết quả**: SQL được sinh ra → DataFrame → Chart hiển thị inline

---

## 📁 Project Structure

```
NL2Viz/
├── app.py                    # Streamlit entrypoint
├── modules/
│   ├── dail_sql.py           # Stage 1 & 2: NL→SQL→DataFrame
│   ├── text_to_python.py     # Stage 3: DataFrame→Chart
│   └── ollama_client.py      # Ollama health check & completion
├── data/
│   └── *.db                  # Sample SQLite databases
├── requirements.txt
└── .env.example
```

---

## 🔧 Module Overview

### `modules/dail_sql.py` — DAIL-SQL

Implement pipeline **Stage 1 + 2**:

- Đọc DB schema (CREATE TABLE statements)
- Build prompt theo chuẩn **DAIL-SQL** (Code Representation + Few-shot Q+SQL pairs)
- Gọi **DeepSeek-Chat API** để sinh SQL
- Execute SQL trên SQLite → trả về `pandas.DataFrame`

```python
from modules.dail_sql import generate_sql, execute_sql

sql = generate_sql(question="Top 5 customers by revenue", db_path="data/sales.db")
df  = execute_sql(sql, db_path="data/sales.db")
```

### `modules/text_to_python.py` — Local-Python-Viz

Implement pipeline **Stage 3**:

- Build Zero-Shot prompt theo cấu trúc Khan et al. (2025): `Context + Requirement + Constraint`
- Gọi **Llama3.2 qua Ollama** để sinh matplotlib code
- Clean code (bỏ markdown fences, ANSI codes) + AST transform
- Sandbox execute → trả về `matplotlib.Figure`

```python
from modules.text_to_python import text_to_figure

fig = text_to_figure(question="Top 5 customers by revenue", df=df)
```

### `modules/ollama_client.py` — Ollama Wrapper

```python
from modules.ollama_client import is_ollama_alive, ollama_complete

if is_ollama_alive():
    response = ollama_complete(prompt="...", model="llama3.2")
```

---

## 🔄 Fallback Chain

Khi Ollama không khả dụng hoặc sinh code lỗi, hệ thống tự động fallback:

```
Ollama running?
  ├── Yes → Llama3.2 local → Success ✅
  │                        → Failed ↓
  └── No  ↓
        DeepSeek key set?
          ├── Yes → DeepSeek API → Success ✅
          │                     → Failed ↓
          └── No  → Mock template chart ✅
```

---

## ⚡ Performance

| Stage | LLM | Latency (typical) | Token/query |
|---|---|---|---|
| Stage 1 — SQL gen | DeepSeek (cloud) | ~1–2s | ~700 |
| Stage 2 — SQL exec | sqlite3 (local) | <100ms | — |
| Stage 3 — Viz code | Llama3.2 (local) | ~3–10s | ~500 |
| **Total** | | **~5–15s** | **~1,200** |

> Latency Stage 3 phụ thuộc vào GPU. Có GPU → ~3s, CPU only → ~10s.

---

## 🔒 Privacy

| Data | Đích đến | Ghi chú |
|---|---|---|
| User question | DeepSeek API | Cloud |
| DB schema | DeepSeek API | Cloud — chỉ schema, **không có data** |
| DataFrame (actual data) | Llama3 local | **Local only** — không rời máy |
| SQL / Python code | Local | Local |

Enterprise data (revenue, customer PII) chỉ xử lý local.

---

## 🐛 Troubleshooting

**Ollama không kết nối được:**
```bash
# Kiểm tra service
curl http://localhost:11434/api/tags

# Khởi động lại
ollama serve
```

**DeepSeek API lỗi 401:**
```bash
# Kiểm tra key đã set chưa
echo $DEEPSEEK_API_KEY
```

**Chart trống / lỗi exec:**
- Thử câu hỏi rõ ràng hơn, ví dụ thêm loại chart: *"Vẽ bar chart top 5 sản phẩm..."*
- Kiểm tra Ollama model đã pull chưa: `ollama list`
