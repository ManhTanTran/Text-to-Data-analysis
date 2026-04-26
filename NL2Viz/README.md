# 📊 NL2Viz — Natural Language to Visualization

Hệ thống end-to-end chuyển câu hỏi tự nhiên thành biểu đồ trực quan, kết hợp:
- **DAIL-SQL** (DeepSeek API) cho Text-to-SQL
- **Local-Python-Viz** (Ollama Llama3 local) cho Text-to-Python visualization

## 🎯 Pipeline tổng quan

```
┌─────────────────┐
│  User Question  │  "Show monthly revenue in 2024"
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────┐
│  Stage 1: DAIL-SQL (DeepSeek API)   │
│  - Code Representation Prompt        │
│  - Few-shot examples                 │
│  - Output: SQL query                 │
└────────┬────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│  Stage 2: SQLite Execution          │
│  - Run SQL                           │
│  - Return DataFrame                  │
└────────┬────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│  Stage 3: Local-Python-Viz          │
│  (Ollama Llama3 local)              │
│  - Zero-shot prompt                  │
│  - Generate matplotlib code          │
│  - Sandbox execute → Figure          │
└────────┬────────────────────────────┘
         │
         ▼
┌─────────────────┐
│   Chart in UI   │
└─────────────────┘
```

## 🏗️ Cấu trúc thư mục

```
NL2Viz/
├── app.py                          # Streamlit UI chính
├── requirements.txt                # Python dependencies
├── README.md                       # File này
│
├── modules/
│   ├── __init__.py
│   ├── dail_sql.py                 # DAIL-SQL (DeepSeek)
│   ├── text_to_python.py           # Text-to-Python (Ollama local)
│   └── ollama_client.py            # Wrapper gọi Ollama API
│
├── demo_db/
│   └── sales_demo.db               # Demo SQLite database
│
├── scripts/
│   ├── create_demo_db.py           # Tạo demo database
│   ├── setup_ollama.sh             # Bash setup Ollama (Linux/Mac)
│   ├── setup_ollama.ps1            # PowerShell setup Ollama (Windows)
│   └── Modelfile_llama3            # Custom Modelfile cho llama3
│
└── docs/
    ├── INSTALL.md                  # Hướng dẫn cài đặt chi tiết
    └── ARCHITECTURE.md             # Kiến trúc hệ thống
```

## 🚀 Quick Start

### 1. Cài đặt Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Cài đặt và chạy Ollama (cho Local-Python-Viz)

**Windows:**
- Tải Ollama từ: https://ollama.com/download
- Cài đặt và chạy:
```powershell
ollama pull llama3.2
ollama serve  # giữ terminal này chạy
```

**Linux/Mac:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2
ollama serve &
```

Kiểm tra:
```bash
curl http://localhost:11434/api/tags
```

### 3. Tạo demo database

```bash
python scripts/create_demo_db.py
```

### 4. Cấu hình DeepSeek API key

**Windows PowerShell:**
```powershell
$env:DEEPSEEK_API_KEY = "sk-your-key-here"
```

**Linux/Mac:**
```bash
export DEEPSEEK_API_KEY="sk-your-key-here"
```

### 5. Chạy app

```bash
streamlit run app.py
```

Truy cập: http://localhost:8501

## 📋 Các câu hỏi mẫu

- "Show monthly revenue in 2024" → Line chart
- "Revenue by product category" → Bar chart
- "Revenue by region" → Pie chart
- "Top 5 best-selling products" → Bar chart
- "Revenue by customer" → Bar chart

## 🔧 Cấu hình

### File `modules/dail_sql.py`
- `DEEPSEEK_API_KEY`: API key (từ env var)
- `MODEL_NAME`: Mặc định `deepseek-chat`

### File `modules/text_to_python.py`
- `OLLAMA_ENDPOINT`: Mặc định `http://127.0.0.1:11434/api/generate`
- `OLLAMA_MODEL`: Mặc định `llama3.2`
- `USE_OLLAMA`: True = Llama3 local, False = DeepSeek API

## 🛠️ Mock Mode

Nếu chưa có DeepSeek API key hoặc Ollama, hệ thống tự động chuyển sang **Mock mode** sinh SQL/code đơn giản theo keyword. Đủ để test UI mà không cần LLM.

## 📚 Tài liệu tham khảo

1. **DAIL-SQL** — Gao et al., 2023. *"Text-to-SQL Empowered by Large Language Models: A Benchmark Evaluation"*
2. **Local-Python-Viz** — *"Evaluating Local Open-Source LLMs for Privacy-Preserving Data Visualization via Zero-Shot Prompting"*
3. **Spider Dataset** — Yu et al., 2018

## ❓ Troubleshooting

Xem [docs/INSTALL.md](docs/INSTALL.md) để biết các lỗi thường gặp và cách fix.
