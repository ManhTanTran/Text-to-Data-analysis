import { AlertTriangle } from "lucide-react";
import { getText } from "../uiText";

export default function StatusBanner({ status, language }) {
  const t = getText(language);
  let message = null;

  if (status.llm_provider === "ollama" && !status.ollama_alive) {
    message = `${t.ollamaWarning} (${status.ollama_model || "llama3.2"})`;
  } else if (status.llm_provider === "deepseek" && !status.has_deepseek_key) {
    message = t.deepseekWarning;
  } else if (status.llm_provider === "auto" && !status.has_deepseek_key && !status.ollama_alive) {
    message = t.autoWarning;
  }

  if (!message) return null;

  return (
    <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800 sm:px-6 lg:px-8">
      <div className="mx-auto flex max-w-6xl items-center gap-2">
        <AlertTriangle className="h-4 w-4 shrink-0" />
        <span>{message}</span>
      </div>
    </div>
  );
}
