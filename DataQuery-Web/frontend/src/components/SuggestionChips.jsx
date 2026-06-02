import { useRef } from "react";
import { ChevronLeft, ChevronRight, Loader2, Sparkles } from "lucide-react";
import { getText } from "../uiText";

function ScrollButton({ direction, onClick }) {
  const Icon = direction === "left" ? ChevronLeft : ChevronRight;

  return (
    <button
      type="button"
      onClick={onClick}
      className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 shadow-sm transition-colors hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700 sm:flex"
      aria-label={direction === "left" ? "Scroll suggestions left" : "Scroll suggestions right"}
    >
      <Icon className="h-4 w-4" />
    </button>
  );
}

export default function SuggestionChips({ suggestions, loading, onSelect, language }) {
  const t = getText(language);
  const scrollerRef = useRef(null);

  const scroll = (direction) => {
    const scroller = scrollerRef.current;
    if (!scroller) return;

    const distance = Math.max(240, Math.floor(scroller.clientWidth * 0.75));
    scroller.scrollBy({
      left: direction === "left" ? -distance : distance,
      behavior: "smooth",
    });
  };

  if (loading) {
    return (
      <div className="border-b border-slate-200 bg-white px-4 py-3 sm:px-6 lg:px-8">
        <div className="mx-auto flex max-w-6xl items-center gap-3 text-sm text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
          <span>{t.suggestionsLoading}</span>
          <div className="hidden flex-1 gap-2 sm:flex">
            {[0, 1, 2].map((item) => (
              <span key={item} className="h-8 w-44 animate-pulse rounded-lg bg-slate-100" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (!suggestions.length) return null;

  return (
    <div className="border-b border-slate-200 bg-white px-4 py-3 sm:px-6 lg:px-8">
      <div className="mx-auto flex max-w-6xl items-center gap-3">
        <div className="flex shrink-0 items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
          <Sparkles className="h-4 w-4 text-amber-500" />
          {t.suggestions}
        </div>

        <ScrollButton direction="left" onClick={() => scroll("left")} />

        <div
          ref={scrollerRef}
          className="min-w-0 flex-1 overflow-x-auto scroll-smooth no-scrollbar"
        >
          <div className="flex w-max gap-2 pr-1">
            {suggestions.map((suggestion, index) => (
              <button
                key={`${suggestion}-${index}`}
                type="button"
                onClick={() => onSelect(suggestion)}
                className="shrink-0 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700"
              >
                {suggestion}
              </button>
            ))}
          </div>
        </div>

        <ScrollButton direction="right" onClick={() => scroll("right")} />
      </div>
    </div>
  );
}
