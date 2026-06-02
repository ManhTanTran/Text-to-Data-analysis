import { MessageSquareText } from "lucide-react";
import { getText } from "../uiText";

export default function EmptyState({ language }) {
  const t = getText(language);

  return (
    <div className="flex min-h-[420px] items-center justify-center px-4 text-center">
      <div className="max-w-md">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-lg border border-blue-100 bg-blue-50 text-blue-600">
          <MessageSquareText className="h-6 w-6" />
        </div>
        <h2 className="mt-4 text-base font-semibold text-slate-950">{t.startTitle}</h2>
        <p className="mt-2 text-sm leading-6 text-slate-500">{t.startBody}</p>
      </div>
    </div>
  );
}
