import { X } from "lucide-react";
import { getText } from "../uiText";
import SchemaPanel from "./SchemaPanel";

export default function SchemaDrawer({
  open,
  onClose,
  session,
  onReset,
  language,
  onLanguageChange,
}) {
  const t = getText(language);

  return (
    <div className={`fixed inset-0 z-50 lg:hidden ${open ? "" : "pointer-events-none"}`}>
      <div
        className={`absolute inset-0 bg-slate-950/40 transition-opacity ${open ? "opacity-100" : "opacity-0"}`}
        onClick={onClose}
      />
      <aside
        className={`absolute inset-y-0 left-0 flex w-[86vw] max-w-sm flex-col border-r border-slate-200 bg-white shadow-xl transition-transform duration-200 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex h-12 items-center justify-between border-b border-slate-200 px-4">
          <span className="text-sm font-semibold text-slate-900">{t.schema}</span>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-700"
            aria-label={t.close}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <SchemaPanel
          session={session}
          onReset={onReset}
          language={language}
          onLanguageChange={onLanguageChange}
        />
      </aside>
    </div>
  );
}
