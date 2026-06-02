import { AlertCircle } from "lucide-react";
import { getText } from "../uiText";
import EmptyState from "./EmptyState";
import ResultCard from "./ResultCard";

function QuerySkeleton() {
  return (
    <div className="mb-4 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <div className="animate-pulse space-y-4">
        <div className="h-4 w-2/3 rounded bg-blue-100" />
        <div className="h-36 rounded-lg bg-slate-100" />
        <div className="h-3 w-1/2 rounded bg-slate-100" />
      </div>
    </div>
  );
}

export default function ResultsPanel({ results, queryLoading, error, language, topRef }) {
  const t = getText(language);

  return (
    <section className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-6xl px-4 py-4 sm:px-6 lg:px-8">
        <div ref={topRef} />

        {queryLoading && <QuerySkeleton />}

        {results.map((result, index) => (
          <ResultCard key={index} result={result} language={language} />
        ))}

        {error && (
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {!queryLoading && results.length === 0 && !error && <EmptyState language={language} />}
      </div>
    </section>
  );
}
