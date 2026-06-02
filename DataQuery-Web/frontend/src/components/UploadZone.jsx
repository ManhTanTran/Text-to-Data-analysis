import { useDropzone } from "react-dropzone";
import { BarChart3, Braces, FileText, Loader2, Table2, Upload } from "lucide-react";
import { getText } from "../uiText";

const ACCEPT = {
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
  "application/vnd.ms-excel": [".xls"],
  "text/csv": [".csv"],
  "text/tab-separated-values": [".tsv"],
  "text/plain": [".txt"],
  "application/json": [".json"],
};

const FORMAT_BADGES = [
  { Icon: Table2, label: "Excel", exts: ".xlsx .xls", className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  { Icon: FileText, label: "CSV / TSV", exts: ".csv .tsv .txt", className: "border-blue-200 bg-blue-50 text-blue-700" },
  { Icon: Braces, label: "JSON", exts: ".json", className: "border-violet-200 bg-violet-50 text-violet-700" },
];

export default function UploadZone({ onUpload, loading, language = "vi" }) {
  const t = getText(language);
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: ACCEPT,
    maxFiles: 1,
    disabled: loading,
    onDrop: (files) => files[0] && onUpload(files[0]),
  });

  return (
    <main className="min-h-screen bg-slate-50 px-4 py-8 text-slate-900 sm:px-6 lg:px-8">
      <div className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-5xl flex-col justify-center">
        <div className="grid items-center gap-8 lg:grid-cols-[0.9fr_1.1fr]">
          <section>
            <div className="inline-flex h-11 w-11 items-center justify-center rounded-lg bg-blue-600 text-white shadow-sm">
              <BarChart3 className="h-6 w-6" />
            </div>
            <h1 className="mt-5 max-w-xl text-3xl font-semibold text-slate-950 sm:text-4xl">
              {t.uploadTitle}
            </h1>
            <p className="mt-3 max-w-lg text-base leading-7 text-slate-600">{t.uploadSubtitle}</p>

            <div className="mt-6">
              <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">{t.formats}</p>
              <div className="flex flex-wrap gap-2">
                {FORMAT_BADGES.map(({ Icon, label, exts, className }) => (
                  <div key={label} className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-medium ${className}`}>
                    <Icon className="h-4 w-4" />
                    <span>{label}</span>
                    <span className="opacity-70">{exts}</span>
                  </div>
                ))}
              </div>
            </div>
          </section>

          <section
            {...getRootProps()}
            className={`rounded-lg border-2 border-dashed bg-white p-8 shadow-sm transition-all sm:p-10 ${
              isDragActive
                ? "border-blue-400 bg-blue-50"
                : "border-slate-200 hover:border-blue-300 hover:bg-blue-50/40"
            } ${loading ? "cursor-not-allowed opacity-70" : "cursor-pointer"}`}
          >
            <input {...getInputProps()} />
            <div className="flex flex-col items-center text-center">
              <div className={`flex h-14 w-14 items-center justify-center rounded-lg ${loading ? "bg-slate-100" : "bg-blue-50"}`}>
                {loading ? (
                  <Loader2 className="h-7 w-7 animate-spin text-blue-600" />
                ) : (
                  <Upload className="h-7 w-7 text-blue-600" />
                )}
              </div>

              <h2 className="mt-5 text-lg font-semibold text-slate-950">
                {loading ? t.uploadLoading : isDragActive ? t.dropActive : t.dropTitle}
              </h2>
              {!loading && <p className="mt-1 text-sm text-slate-500">{t.dropSubtitle}</p>}

              <div className="mt-6 grid w-full gap-2 text-left sm:grid-cols-3">
                {[
                  ["Schema", "Auto-read columns"],
                  ["SQL", "RAG examples"],
                  ["Charts", "Local render"],
                ].map(([title, body]) => (
                  <div key={title} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                    <p className="text-xs font-semibold text-slate-700">{title}</p>
                    <p className="mt-1 text-xs text-slate-500">{body}</p>
                  </div>
                ))}
              </div>
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
