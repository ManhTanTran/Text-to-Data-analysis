# 🛠️ NL2Viz — Hướng dẫn cài đặt chi tiết

## 1. Yêu cầu hệ thống

- **Python 3.9+** (khuyến nghị 3.11)
- **8GB RAM** (cho Llama3.2 chạy local)
- **3GB ổ cứng** trống (cho Ollama model)
- **Internet** (lần đầu để pull model + gọi DeepSeek)

## 2. Cài đặt từng bước

### Bước 1 — Clone hoặc copy folder NL2Viz

```bash
cd path/to/your/workspace
# Copy NL2Viz folder vào đây
```

### Bước 2 — Tạo Python environment

**Windows:**
```powershell
cd NL2Viz
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Linux/Mac:**
```bash
cd NL2Viz
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Bước 3 — Cài Ollama (cho Local-Python-Viz)

**Windows:**

1. Tải installer: https://ollama.com/download/windows
2. Cài đặt
3. Mở PowerShell mới, chạy:
```powershell
ollama pull llama3.2
```
4. Khởi động server (giữ terminal chạy):
```powershell
ollama serve
```

**Hoặc dùng setup script:**
```powershell
.\scripts\setup_ollama.ps1
```

**Linux/Mac:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2
ollama serve &
```

**Hoặc dùng setup script:**
```bash
bash scripts/setup_ollama.sh
```

### Bước 4 — Tạo demo database

```bash
python scripts/create_demo_db.py
```

Output:
```
✅ Database created: NL2Viz/demo_db/sales_demo.db
```

### Bước 5 — Cấu hình DeepSeek API key (cho DAIL-SQL)

Lấy key tại: https://platform.deepseek.com/api_keys

**Windows PowerShell:**
```powershell
$env:DEEPSEEK_API_KEY = "sk-your-key-here"
```

**Linux/Mac:**
```bash
export DEEPSEEK_API_KEY="sk-your-key-here"
```

> 💡 **Lưu ý:** Env var chỉ tồn tại trong terminal hiện tại. Để vĩnh viễn, set trong:
> - Windows: System Environment Variables (GUI)
> - Linux/Mac: `~/.bashrc` hoặc `~/.zshrc`

### Bước 6 — Chạy app

```bash
streamlit run app.py
```

Truy cập: http://localhost:8501

## 3. Verification

Mở app, kiểm tra sidebar:
- ✅ "DeepSeek API connected" — Stage 1 OK
- ✅ "Ollama running" — Stage 3 OK

## 4. Troubleshooting

### Lỗi: `ModuleNotFoundError: No module named 'streamlit'`
- Quên activate venv. Chạy lại: `.\venv\Scripts\Activate.ps1` (Windows) hoặc `source venv/bin/activate` (Linux/Mac)

### Lỗi: "Cannot connect to Ollama at http://127.0.0.1:11434"
- Ollama server chưa chạy. Mở terminal mới: `ollama serve`
- Kiểm tra: `curl http://localhost:11434/api/tags` phải trả về JSON

### Lỗi: "No models found" trong sidebar
- Chưa pull model. Chạy: `ollama pull llama3.2`
- Verify: `ollama list`

### Lỗi: SQL execution failed
- Database chưa tạo: `python scripts/create_demo_db.py`
- Kiểm tra path: `demo_db/sales_demo.db` tồn tại?

### Lỗi: DeepSeek API error
- Key sai/hết hạn → tạo key mới
- Hoặc dùng mock mode: tick "Use mock SQL" trong sidebar

### Llama3 sinh code chậm
- Kiểm tra GPU: `ollama list` cho thấy `num_gpu` không?
- Custom Modelfile: `ollama create llama3-fast -f scripts/Modelfile_llama3`
- Đổi `OLLAMA_MODEL = "llama3-fast"` trong `modules/ollama_client.py`

### Streamlit cảnh báo "matplotlib backend"
- Lờ đi, không ảnh hưởng. App đã set `matplotlib.use("Agg")` để render PNG.

## 5. Customize

### Đổi LLM cho DAIL-SQL
Trong `modules/dail_sql.py`:
```python
DEEPSEEK_BASE_URL = "https://api.openai.com/v1"  # OpenAI
MODEL_NAME = "gpt-4o"
DEEPSEEK_API_KEY = os.getenv("OPENAI_API_KEY")
```

### Đổi DB của bạn
Thay `DB_PATH` trong `app.py`:
```python
DB_PATH = "/path/to/your/db.sqlite"
```

Vẫn dùng được — DAIL-SQL tự đọc schema runtime.

### Thêm few-shot examples
Sửa `_FEW_SHOT_EXAMPLES` trong `modules/dail_sql.py` để thêm ví dụ phù hợp với domain của bạn.
