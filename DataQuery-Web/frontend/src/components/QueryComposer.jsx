import { useState } from "react";
import { Loader2, SendHorizontal } from "lucide-react";
import { getText } from "../uiText";

export default function QueryComposer({ onSubmit, loading, language }) {
  const t = getText(language);
  const [value, setValue] = useState("");

  const submit = () => {
    const question = value.trim();
    if (!question || loading) return;
    onSubmit(question);
    setValue("");
  };

  return (
    <footer className="shrink-0 border-t border-slate-200 bg-white/95 px-4 py-3 backdrop-blur sm:px-6 lg:px-8">
      <div className="mx-auto max-w-6xl">
        <div
          className={`flex items-center gap-3 rounded-lg border bg-white px-3 py-2 shadow-sm transition-colors ${
            loading
              ? "border-slate-200 bg-slate-50"
              : "border-slate-200 focus-within:border-blue-400 focus-within:ring-2 focus-within:ring-blue-100"
          }`}
        >
          <input
            className="min-w-0 flex-1 bg-transparent px-1 text-sm text-slate-900 outline-none placeholder:text-slate-400"
            placeholder={t.askPlaceholder}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && !event.shiftKey && submit()}
            disabled={loading}
          />
          <button
            type="button"
            onClick={submit}
            disabled={!value.trim() || loading}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-600 text-white transition-colors hover:bg-blue-700 disabled:bg-slate-200 disabled:text-slate-400"
            aria-label="Send"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <SendHorizontal className="h-4 w-4" />}
          </button>
        </div>
        <p className="mt-2 text-center text-xs text-slate-400">{t.composerHint}</p>
      </div>
    </footer>
  );
}
