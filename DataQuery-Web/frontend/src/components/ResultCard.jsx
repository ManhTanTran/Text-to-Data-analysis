import { useMemo, useState } from "react";
import {
  AlertCircle,
  BarChart2,
  Check,
  Code,
  Copy,
  Database,
  MessageSquare,
  Table,
  TrendingUp,
} from "lucide-react";
import { getText } from "../uiText";

function Badge({ children, tone = "slate" }) {
  const tones = {
    slate: "border-slate-200 bg-slate-50 text-slate-600",
    blue: "border-blue-200 bg-blue-50 text-blue-700",
    emerald: "border-emerald-200 bg-emerald-50 text-emerald-700",
    amber: "border-amber-200 bg-amber-50 text-amber-700",
    red: "border-red-200 bg-red-50 text-red-700",
  };

  return (
    <span className={`inline-flex items-center rounded-lg border px-2.5 py-1 text-xs font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}

function CodeBlock({ label, value, copied, onCopy, maxHeight = "max-h-96" }) {
  return (
    <section>
      <div className="mb-2 flex items-center justify-between gap-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
        <button
          type="button"
          onClick={onCopy}
          className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition-colors hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700"
          title="Copy"
        >
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        </button>
      </div>
      <pre className={`${maxHeight} overflow-auto rounded-lg bg-slate-950 p-4 text-xs leading-relaxed text-slate-100 shadow-inner`}>
        <code>{value || ""}</code>
      </pre>
    </section>
  );
}

function ErrorBanner({ title, message, tone = "red" }) {
  const styles =
    tone === "amber"
      ? "border-amber-200 bg-amber-50 text-amber-800"
      : "border-red-200 bg-red-50 text-red-700";

  return (
    <div className={`flex items-start gap-2 border-b px-5 py-3 ${styles}`}>
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0">
        <p className="text-sm font-semibold">{title}</p>
        {message && <p className="mt-1 break-words font-mono text-xs opacity-80">{message}</p>}
      </div>
    </div>
  );
}

export default function ResultCard({ result, language = "vi" }) {
  const t = getText(language);
  const rows = Array.isArray(result.rows) ? result.rows : [];
  const columns = Array.isArray(result.columns) ? result.columns : [];
  const hasChart = Boolean(result.chart_base64);
  const hasMetric = Boolean(result.single_metric);
  const rowCount = Number.isFinite(result.row_count) ? result.row_count : rows.length;
  const questionText = String(result.question || "").toLowerCase();
  const firstColumn = String(columns[0] || "").toLowerCase();
  const looksLikeTrendTable =
    columns.length >= 3 &&
    (questionText.includes("tăng giảm") ||
      questionText.includes("xu hướng") ||
      questionText.includes("theo thời gian") ||
      questionText.includes("trend") ||
      questionText.includes("over time") ||
      firstColumn.includes("thời gian") ||
      firstColumn.includes("time"));
  const defaultTab = looksLikeTrendTable ? "table" : hasChart ? "chart" : hasMetric ? "metric" : "table";
  const [tab, setTab] = useState(defaultTab);
  const [copiedKey, setCopiedKey] = useState(null);

  const tabs = useMemo(() => {
    if (hasMetric) {
      return [
        { id: "metric", label: t.result, Icon: TrendingUp },
        { id: "table", label: t.table, Icon: Table },
        { id: "sql", label: t.sql, Icon: Code },
      ];
    }

    const baseTabs = [
      { id: "table", label: t.table, Icon: Table },
      { id: "sql", label: t.sql, Icon: Code },
    ];

    return hasChart ? [{ id: "chart", label: t.chart, Icon: BarChart2 }, ...baseTabs] : baseTabs;
  }, [hasChart, hasMetric, t.chart, t.result, t.sql, t.table]);

  const copyText = async (value, key) => {
    if (!value || !navigator.clipboard) return;
    await navigator.clipboard.writeText(value);
    setCopiedKey(key);
    window.setTimeout(() => setCopiedKey(null), 1400);
  };

  return (
    <article className="mb-4 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm fade-slide">
      <header className="border-b border-slate-200 bg-slate-50 px-5 py-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex min-w-0 gap-3">
            <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
              <MessageSquare className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <h2 className="break-words text-sm font-semibold leading-6 text-slate-950">{result.question}</h2>
              {result.llm_provider && (
                <p className="mt-1 text-xs text-slate-500">
                  {t.provider}: <span className="font-medium text-slate-700">{result.llm_provider}</span>
                </p>
              )}
            </div>
          </div>

          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {result.rag_used && <Badge tone="emerald">{t.rag}</Badge>}
            {result.mock_mode && <Badge tone="amber">{t.mockSql}</Badge>}
            <Badge tone={rowCount > 0 ? "blue" : "slate"}>
              {rowCount} {t.rows}
            </Badge>
          </div>
        </div>
      </header>

      {result.sql_error && <ErrorBanner title={t.sqlError} message={result.sql_error} />}
      {result.llm_error && <ErrorBanner title={t.llmError} message={result.llm_error} tone="amber" />}

      <nav className="flex gap-1 overflow-x-auto border-b border-slate-200 px-4 pt-3 no-scrollbar" aria-label="Result tabs">
        {tabs.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={`flex h-10 shrink-0 items-center gap-2 rounded-t-lg border-b-2 px-3 text-xs font-semibold transition-colors ${
              tab === id
                ? "border-blue-600 bg-blue-50 text-blue-700"
                : "border-transparent text-slate-500 hover:bg-slate-50 hover:text-slate-800"
            }`}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        ))}
      </nav>

      <div className="p-4 sm:p-5">
        {tab === "chart" && hasChart && (
          <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
            <img
              src={`data:image/png;base64,${result.chart_base64}`}
              alt={t.chart}
              className="h-auto w-full"
            />
          </div>
        )}

        {tab === "metric" && hasMetric && (
          <div className="flex min-h-48 items-center justify-center rounded-lg border border-slate-200 bg-slate-50 px-4 py-8">
            <div className="text-center">
              <p className="text-4xl font-semibold text-blue-700 sm:text-5xl">{result.single_metric}</p>
              <p className="mt-2 text-sm font-medium text-slate-500">{columns[0] || t.result}</p>
            </div>
          </div>
        )}

        {tab === "table" && (
          <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
            {rows.length > 0 && columns.length > 0 ? (
              <div className="max-h-[28rem] overflow-auto">
                <table className="w-full min-w-max text-left text-sm">
                  <thead className="sticky top-0 z-10 bg-slate-50">
                    <tr>
                      {columns.map((column) => (
                        <th
                          key={column}
                          className="border-b border-slate-200 px-4 py-3 text-xs font-semibold uppercase tracking-wide text-slate-500"
                        >
                          {column}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.slice(0, 100).map((row, rowIndex) => (
                      <tr key={rowIndex} className={rowIndex % 2 === 0 ? "bg-white" : "bg-slate-50/60"}>
                        {columns.map((column) => {
                          const value = row[column];
                          return (
                            <td
                              key={column}
                              className="max-w-[18rem] truncate border-b border-slate-100 px-4 py-3 text-sm text-slate-700"
                              title={value === null || value === undefined ? "null" : String(value)}
                            >
                              {value === null || value === undefined ? (
                                <span className="text-slate-300">null</span>
                              ) : (
                                String(value)
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="flex min-h-40 flex-col items-center justify-center gap-2 bg-slate-50 px-4 py-8 text-center">
                <Database className="h-6 w-6 text-slate-300" />
                <p className="text-sm font-medium text-slate-500">{t.noData}</p>
              </div>
            )}

            {rows.length > 100 && (
              <div className="border-t border-slate-200 bg-slate-50 px-4 py-2 text-center text-xs text-slate-500">
                {t.showingRows} 100/{rows.length} {t.rows}
              </div>
            )}
          </div>
        )}

        {tab === "sql" && (
          <div className="space-y-4">
            <CodeBlock
              label={t.finalSql}
              value={result.sql}
              copied={copiedKey === "sql"}
              onCopy={() => copyText(result.sql, "sql")}
              maxHeight="max-h-80"
            />

            {result.rag_used && result.llm_prompt && (
              <CodeBlock
                label={t.ragPrompt}
                value={result.llm_prompt}
                copied={copiedKey === "prompt"}
                onCopy={() => copyText(result.llm_prompt, "prompt")}
              />
            )}

            {result.rag_used && Array.isArray(result.retrieved_examples) && result.retrieved_examples.length > 0 && (
              <details className="rounded-lg border border-slate-200 bg-slate-50">
                <summary className="cursor-pointer px-4 py-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  RAG examples ({result.retrieved_examples.length})
                </summary>
                <div className="space-y-3 border-t border-slate-200 p-4">
                  {result.retrieved_examples.map((example, index) => (
                    <div key={`${example.db_id || "example"}-${index}`} className="rounded-lg bg-white p-3 text-xs">
                      <div className="mb-2 flex flex-wrap items-center gap-2 text-slate-500">
                        <Badge>{example.db_id || "spider"}</Badge>
                        {Number.isFinite(example.score) && <Badge tone="blue">{example.score.toFixed(2)}</Badge>}
                      </div>
                      <p className="font-medium text-slate-700">{example.question}</p>
                      <pre className="mt-2 max-h-32 overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-100">
                        <code>{example.query}</code>
                      </pre>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </div>
    </article>
  );
}
