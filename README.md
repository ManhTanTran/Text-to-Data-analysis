# 📊 Text-to-Data-analysis

> **NL2Viz** — Hỏi bằng tiếng tự nhiên, nhận lại biểu đồ tức thì.  
> Pipeline 2-stage LLM: DeepSeek (SQL) × Llama3 local (Visualization)

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.x-FF4B4B?logo=streamlit)](https://streamlit.io/)
[![Ollama](https://img.shields.io/badge/Ollama-local-black?logo=ollama)](https://ollama.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 🗂️ Repository Structure

```
Text-to-Data-analysis/
├── NL2Viz/          # 🚀 Production app — Streamlit + 2-stage LLM pipeline
│   ├── app.py
│   ├── modules/
│   │   ├── dail_sql.py          # Stage 1: NL → SQL (DeepSeek)
│   │   ├── text_to_python.py    # Stage 3: DataFrame → Chart (Llama3)
│   │   └── ollama_client.py     # Ollama health check & wrapper
│   ├── data/                    # Sample SQLite databases
│   ├── requirements.txt
│   └── README.md
│
├── research/        # 📑 Paper, benchmark & evaluation results
│   ├── report/      # Báo cáo nghiên cứu (PDF / LaTeX)
│   ├── benchmark/   # Kết quả đánh giá DAIL-SQL & Local-Python-Viz
│   └── README.md
│
└── ARCHITECTURE.md  # Thiết kế hệ thống chi tiết
```

---

## ✨ Highlights

| Tính năng | Chi tiết |
|---|---|
| 🗣️ Natural Language input | Hỏi bằng tiếng Việt hoặc tiếng Anh |
| 🧠 2-stage LLM pipeline | DeepSeek → SQL, Llama3 → Chart code |
| 🔒 Privacy-first | Data thực tế xử lý **100% local**, không gửi cloud |
| 📊 Auto visualization | Bar, Line, Pie, Scatter tự động sinh ra |
| 🔄 Fallback chain | Ollama → DeepSeek → Mock template |

---

## ⚡ Quick Start

### 1. Clone & cài dependencies

```bash
git clone https://github.com/ManhTanTran/Text-to-Data-analysis.git
cd Text-to-Data-analysis/NL2Viz
pip install -r requirements.txt
```

### 2. Cấu hình API key

```bash
# .env hoặc export trực tiếp
export DEEPSEEK_API_KEY="your_deepseek_key"
```

### 3. Khởi động Ollama + pull model

```bash
ollama serve
ollama pull llama3.2
```

### 4. Chạy app

```bash
streamlit run app.py
```

Mở trình duyệt tại `http://localhost:8501` và đặt câu hỏi như:

> *"Tổng doanh thu theo từng tháng trong năm ngoái là bao nhiêu?"*

---

## 🏗️ How It Works

```
User Question (NL)
       │
       ▼
  [Stage 1] DAIL-SQL ──── DeepSeek API ──→  SQL Query
       │
       ▼
  [Stage 2] SQL Executor ─ sqlite3/pandas ─→ DataFrame
       │
       ▼
  [Stage 3] Local-Python-Viz ── Llama3 ──→  matplotlib Figure
       │
       ▼
   📊 Chart (inline Streamlit)
```

Chi tiết đầy đủ: xem [`ARCHITECTURE.md`](ARCHITECTURE.md)

---

## 📚 Research

Dự án được xây dựng dựa trên 2 paper:

- **DAIL-SQL** (Gao et al., 2023) — *Efficient Prompt Engineering for Text-to-SQL*
- **Local-Python-Viz** (Khan et al., 2025) — *Zero-Shot Chart Generation with Local LLMs*

Kết quả benchmark và báo cáo nghiên cứu đầy đủ: xem [`research/`](research/README.md)

---

## 📄 License

MIT © 2025 [ManhTanTran](https://github.com/ManhTanTran)
