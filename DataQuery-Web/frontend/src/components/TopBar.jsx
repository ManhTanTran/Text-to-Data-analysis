import { Database, Menu, Server } from "lucide-react";
import { getText } from "../uiText";

function ProviderPill({ status }) {
  const provider = status.llm_provider || "auto";
  const isReady = provider === "ollama" ? status.ollama_alive : true;

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium ${
        isReady
          ? "border-emerald-200 bg-emerald-50 text-emerald-700"
          : "border-amber-200 bg-amber-50 text-amber-700"
      }`}
    >
      <Server className="h-3.5 w-3.5" />
      {provider}
    </span>
  );
}

export default function TopBar({ session, status, language, onOpenSchema }) {
  const t = getText(language);

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white/90 px-4 backdrop-blur sm:px-6 lg:px-8">
      <div className="flex min-w-0 items-center gap-3">
        <button
          type="button"
          onClick={onOpenSchema}
          className="flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 lg:hidden"
          aria-label={t.openSchema}
        >
          <Menu className="h-4 w-4" />
        </button>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Database className="hidden h-4 w-4 text-slate-400 sm:block" />
            <h1 className="truncate text-sm font-semibold text-slate-950 sm:text-base">
              {session.filename}
            </h1>
          </div>
          <p className="hidden text-xs text-slate-500 sm:block">
            {Object.keys(session.schema).length} table{Object.keys(session.schema).length === 1 ? "" : "s"} · {t.provider}: {status.llm_provider || "auto"}
          </p>
        </div>
      </div>

      <div className="flex items-center gap-2">
        {status.rag_enabled && (
          <span className="hidden rounded-lg border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700 sm:inline-flex">
            {t.rag} {status.rag_index_ready ? status.rag_example_count : "off"}
          </span>
        )}
        <ProviderPill status={status} />
      </div>
    </header>
  );
}
