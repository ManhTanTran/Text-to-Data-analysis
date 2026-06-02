import { useState } from "react";
import {
  BarChart3,
  ChevronDown,
  ChevronRight,
  Database,
  FileSpreadsheet,
  Languages,
  RefreshCw,
} from "lucide-react";
import { getText } from "../uiText";

function LanguageToggle({ language, onLanguageChange }) {
  return (
    <div className="grid grid-cols-2 gap-1 rounded-lg bg-slate-100 p-1">
      {[
        ["vi", "VI"],
        ["en", "EN"],
      ].map(([value, label]) => (
        <button
          key={value}
          type="button"
          onClick={() => onLanguageChange?.(value)}
          className={`h-8 rounded-md text-xs font-semibold transition-colors ${
            language === value
              ? "bg-white text-blue-700 shadow-sm"
              : "text-slate-500 hover:text-slate-700"
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export default function SchemaPanel({ session, onReset, language = "vi", onLanguageChange }) {
  const t = getText(language);
  const [open, setOpen] = useState(Object.keys(session.schema));

  const toggle = (table) =>
    setOpen((prev) => (prev.includes(table) ? prev.filter((tName) => tName !== table) : [...prev, table]));

  return (
    <div className="flex h-full min-h-0 flex-col bg-white">
      <div className="border-b border-slate-200 p-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-600 text-white shadow-sm">
            <BarChart3 className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-slate-950">{t.appName}</p>
            <p className="text-xs text-slate-500">Text-to-SQL analytics</p>
          </div>
        </div>

        <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3">
          <div className="flex items-start gap-2">
            <FileSpreadsheet className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
            <div className="min-w-0">
              <p className="truncate text-xs font-medium text-slate-700" title={session.filename}>
                {session.filename}
              </p>
              <p className="mt-0.5 text-xs text-slate-400">
                {Object.keys(session.schema).length} table{Object.keys(session.schema).length === 1 ? "" : "s"}
              </p>
            </div>
          </div>
        </div>
      </div>

      <div className="border-b border-slate-200 p-4">
        <div className="mb-2 flex items-center gap-2">
          <Languages className="h-4 w-4 text-slate-400" />
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t.language}</span>
        </div>
        <LanguageToggle language={language} onLanguageChange={onLanguageChange} />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mb-3 flex items-center gap-2">
          <Database className="h-4 w-4 text-slate-400" />
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t.schema}</span>
        </div>

        <div className="space-y-1.5">
          {Object.entries(session.schema).map(([table, cols]) => {
            const isOpen = open.includes(table);
            return (
              <div key={table} className="rounded-lg border border-slate-200 bg-white">
                <button
                  type="button"
                  onClick={() => toggle(table)}
                  className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition-colors hover:bg-slate-50"
                >
                  {isOpen ? (
                    <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
                  ) : (
                    <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" />
                  )}
                  <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-800">{table}</span>
                  <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-500">
                    {cols.length}
                  </span>
                </button>

                {isOpen && (
                  <div className="border-t border-slate-100 px-3 py-2">
                    <div className="space-y-1">
                      {cols.map((col) => (
                        <div
                          key={col}
                          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-slate-600 hover:bg-blue-50 hover:text-blue-700"
                          title={col}
                        >
                          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-blue-300" />
                          <span className="min-w-0 truncate">{col}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="border-t border-slate-200 p-4">
        <button
          type="button"
          onClick={onReset}
          className="flex h-9 w-full items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 transition-colors hover:border-red-200 hover:bg-red-50 hover:text-red-700"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          {t.newFile}
        </button>
      </div>
    </div>
  );
}
