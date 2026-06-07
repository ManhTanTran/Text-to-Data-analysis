# Text-to-Data-analysis

Conversational AI system for data analysis: users upload tabular data, ask questions in natural language, and receive SQL results plus charts. The active application in this repository is `DataQuery-Web`, a FastAPI + React implementation of an NL2SQL + NL2Viz pipeline.

## Highlights

| Feature | Description |
|---|---|
| Natural-language data queries | Ask questions in Vietnamese or English over uploaded Excel/CSV data |
| NL2SQL pipeline | Uses deterministic planning when possible and an LLM SQL path for fallback generation |
| RAG for Text-to-SQL | Retrieves few-shot question-SQL examples from Spider train before prompting the LLM |
| Embedding-based retrieval | Encodes user questions and Spider examples with `sentence-transformers`, ranks by cosine similarity, and injects nearest examples into the prompt |
| Auto visualization | Produces chart-ready outputs for common aggregation and ranking queries |
| Evaluation scripts | Includes app-level benchmark and Spider retrieval benchmark scripts |

## Current Results

### Spider 200-case Retrieval Benchmark

Evaluation setup: Spider dev subset, 200 cases, DeepSeek provider, candidate pool 3000, DAIL threshold 0.85.

| Mode | EX |
|---|---:|
| no-RAG | 67.50% |
| BM25 retrieval | 71.00% |
| LLM + embedding retrieval | **71.50%** |
| DAIL-style retrieval | 67.00% |

`EX` means execution accuracy: the generated SQL is counted as correct when it returns the same result as the gold SQL after execution.

### DataQuery-Web App Benchmark

Evaluation setup: 40 built-in sales-analysis cases.

| Metric | Score |
|---|---:|
| Overall | **96.31/100** |
| Execution | 30.00/30 |
| Result columns/intent | 28.31/30 |
| Value correctness | 19.00/20 |
| Chart behavior | 9.00/10 |
| Stability | 10.00/10 |

## Repository Structure

```text
Text-to-Data-analysis/
├── DataQuery-Web/
│   ├── backend/
│   │   ├── main.py
│   │   ├── modules/
│   │   │   ├── nl2sql.py
│   │   │   ├── rag.py
│   │   │   ├── dail_sql.py
│   │   │   ├── intent_planner.py
│   │   │   ├── relational_schema.py
│   │   │   ├── semantic_schema.py
│   │   │   └── viz.py
│   │   ├── scripts/
│   │   │   ├── evaluate_app.py
│   │   │   ├── evaluate_spider.py
│   │   │   ├── benchmark_spider_matrix.py
│   │   │   └── build_rag_index.py
│   │   ├── eval/
│   │   └── requirements.txt
│   └── frontend/
│       ├── src/
│       └── package.json
└── NL2Viz/
    └── Legacy Streamlit prototype
```

## Quick Start

### 1. Backend

```bash
cd DataQuery-Web/backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn main:app --reload --port 8000
```

Set your DeepSeek API key in `DataQuery-Web/backend/.env`:

```env
LLM_PROVIDER=auto
DEEPSEEK_API_KEY=your_deepseek_api_key_here
DEEPSEEK_MODEL=deepseek-v4-flash
RAG_ENABLED=true
RAG_RETRIEVAL_MODE=embedding
RAG_TOP_K=4
RAG_CANDIDATE_POOL=3000
```

### 2. Frontend

```bash
cd DataQuery-Web/frontend
npm install
npm run dev
```

Open the Vite URL shown in the terminal, usually `http://localhost:5173`.

## RAG Method

The RAG component uses the Spider training split as an example corpus. Each Spider example stores a natural-language question, SQL query, database identifier, schema information, and lightweight SQL features.

For the best-performing `LLM + embedding` mode:

1. Encode Spider train questions offline with `sentence-transformers`.
2. Encode the user's input question at inference time.
3. Rank Spider examples by cosine similarity.
4. Select the nearest question-SQL examples.
5. Insert those examples into the LLM prompt as few-shot demonstrations.
6. Ask the LLM to generate SQL for the current uploaded schema.

This retrieval mode achieved 71.50% EX on the latest 200-case Spider benchmark, compared with 67.50% for no-RAG.

## Evaluation

Run the app benchmark:

```bash
cd DataQuery-Web/backend
python scripts/evaluate_app.py --provider deepseek
```

Run the Spider retrieval matrix:

```bash
cd DataQuery-Web/backend
python scripts/benchmark_spider_matrix.py --provider deepseek --limit 200 --candidate-pool 3000 --modes no_rag,bm25,embedding,dail
```

If Spider data is not found automatically, set:

```env
SPIDER_DATA_DIR=C:\path\to\spider_data
```

## Notes

- `DataQuery-Web/backend/.env`, Python virtual environments, `node_modules`, and build outputs are ignored by Git.
- Keep API keys out of commits. Use `.env` locally and `.env.example` for shared configuration.
- The legacy `NL2Viz` directory is kept as the earlier Streamlit prototype; new development should target `DataQuery-Web`.

## License

MIT © 2026 ManhTanTran
