# 📑 Research

Tài liệu nghiên cứu, benchmark và đánh giá cho hệ thống **NL2Viz**.

---

## 📁 Structure

```
research/
├── report/           # Báo cáo nghiên cứu
│   ├── report.pdf    # Bản PDF cuối
│   └── src/          # LaTeX / Word source (nếu có)
│
└── benchmark/        # Kết quả đánh giá thực nghiệm
    ├── dail_sql/     # Benchmark Stage 1: NL→SQL accuracy
    └── local_viz/    # Benchmark Stage 3: Chart generation accuracy
```

---

## 📖 Research Papers

Dự án implement và kết hợp 2 phương pháp:

### 1. DAIL-SQL (Stage 1 — SQL Generation)

> Gao, D., et al. (2023). **DAIL-SQL: Efficient Prompt Engineering for Large Language Model Text-to-SQL.** *arXiv:2308.15363*

**Đóng góp chính:**
- **Code Representation Prompt** — Biểu diễn schema bằng CREATE TABLE statements thay vì mô tả tự nhiên
- **DAIL Organization** — Chọn few-shot examples bằng skeleton-similarity (câu hỏi tương tự → SQL pattern tương tự)
- Đạt **82.6% execution accuracy** trên benchmark Spider với GPT-4

**Cách NL2Viz áp dụng:**
- Dùng Code Representation để build DB schema prompt
- Few-shot examples cố định theo domain (thay vì skeleton-similarity selector động)
- LLM: DeepSeek-Chat thay vì GPT-4 (cost hiệu quả hơn ~20x)

---

### 2. Local-Python-Viz (Stage 3 — Visualization Code Generation)

> Khan, A., et al. (2025). **Zero-Shot Chart Generation from Tabular Data Using Local LLMs.** *(Local-Python-Viz)*

**Đóng góp chính:**
- **Zero-Shot 3-part prompt**: `Context` (mô tả DataFrame) + `Requirement` (yêu cầu từ user) + `Constraint` (output format)
- Đạt **79–95% accuracy** trên các chart phổ biến với GPT-3.5+
- Llama3 8B đạt ~**70% accuracy** trên Bar/Pie/Line charts

**Cách NL2Viz áp dụng:**
- Implement đúng cấu trúc Zero-Shot prompt 3 phần
- LLM: Llama3.2 chạy local qua Ollama
- Thêm fallback chain và AST-transform để tăng robustness

---

## 📊 Benchmark Results

### Stage 1 — SQL Generation Accuracy

Đánh giá trên tập câu hỏi tự tổng hợp từ sample databases.

| Model | Prompt Style | Exact Match | Execution Accuracy |
|---|---|---|---|
| DeepSeek-Chat | Zero-shot | ~58% | ~62% |
| DeepSeek-Chat | DAIL-SQL (few-shot) | ~74% | **~79%** |
| GPT-4 (ref) | DAIL-SQL | ~84% | ~83% |

> Chi tiết: [`benchmark/dail_sql/`](benchmark/dail_sql/)

---

### Stage 3 — Chart Generation Accuracy

Đánh giá bằng cách kiểm tra: code sinh ra có chạy được không + chart có đúng loại không.

| Model | Chart Types | Runnable Rate | Correct Type Rate |
|---|---|---|---|
| Llama3.2 (local) | Bar, Line, Pie | ~85% | ~72% |
| Llama3.2 (local) | Scatter, Mixed | ~70% | ~61% |
| DeepSeek (fallback) | Bar, Line, Pie | ~92% | ~88% |

> Chi tiết: [`benchmark/local_viz/`](benchmark/local_viz/)

---

## 🔬 Key Findings

**1. Few-shot cải thiện SQL accuracy đáng kể (+17%)** so với zero-shot, đặc biệt với JOIN queries và aggregate functions phức tạp.

**2. Zero-shot đủ tốt cho Viz code** (~72–85% runnable) vì task matplotlib chart là pattern LLM đã quen thuộc từ pre-training.

**3. Privacy-performance tradeoff hợp lý:** Llama3 local thua DeepSeek ~16% trên chart accuracy, nhưng đảm bảo data không rời khỏi máy — trade-off chấp nhận được cho enterprise use case.

**4. Fallback chain tăng overall reliability** từ ~72% (Llama3 only) lên ~95% (Llama3 + DeepSeek fallback + mock template).

---

## 📄 Report

Báo cáo nghiên cứu đầy đủ (phương pháp, thực nghiệm, kết quả, kết luận):

👉 [`report/report.pdf`](report/report.pdf)

Nội dung bao gồm:
- Tổng quan bài toán Text-to-Visualization
- Review hai paper DAIL-SQL và Local-Python-Viz
- Thiết kế pipeline NL2Viz 3-stage
- Kết quả thực nghiệm và phân tích
- Hướng phát triển tiếp theo

---

## 📚 References

```
[1] Gao, D., Wang, H., Li, Y., Sun, X., Qian, Y., Ding, B., & Zhou, J. (2023).
    Text-to-SQL Empowered by Large Language Models: A Benchmark Evaluation.
    arXiv preprint arXiv:2308.15363.

[2] Khan, A., et al. (2025).
    Local-Python-Viz: Zero-Shot Data Visualization Using Local Large Language Models.

[3] Yu, T., Zhang, R., Yang, K., et al. (2018).
    Spider: A Large-Scale Human-Labeled Dataset for Complex and Cross-Domain
    Semantic Parsing and Text-to-SQL Task. EMNLP 2018.
```
