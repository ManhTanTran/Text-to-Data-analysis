import { useEffect, useRef, useState } from "react";
import { AlertCircle } from "lucide-react";
import { getStatus, getSuggestions, runQuery, uploadFile } from "./api";
import AppShell from "./components/AppShell";
import QueryComposer from "./components/QueryComposer";
import ResultsPanel from "./components/ResultsPanel";
import SuggestionChips from "./components/SuggestionChips";
import UploadZone from "./components/UploadZone";
import { getText } from "./uiText";

export default function App() {
  const [session, setSession] = useState(null);
  const [results, setResults] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [language, setLanguage] = useState("vi");
  const [uploadLoading, setUploadLoading] = useState(false);
  const [queryLoading, setQueryLoading] = useState(false);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [status, setStatus] = useState({
    has_deepseek_key: true,
    llm_provider: "auto",
    ollama_alive: false,
    ollama_model: "llama3.2",
    rag_enabled: false,
    rag_index_ready: false,
    rag_example_count: 0,
  });
  const [error, setError] = useState(null);
  const topRef = useRef(null);

  useEffect(() => {
    getStatus().then(setStatus).catch(() => {});
  }, []);

  const loadSuggestions = (sessionId, nextLanguage) => {
    setSuggestions([]);
    setSuggestLoading(true);
    getSuggestions(sessionId, nextLanguage)
      .then(setSuggestions)
      .catch(() => {})
      .finally(() => setSuggestLoading(false));
  };

  const handleUpload = async (file) => {
    const t = getText(language);
    setError(null);
    setUploadLoading(true);
    try {
      const data = await uploadFile(file);
      setSession({ id: data.session_id, schema: data.schema, filename: data.filename });
      setResults([]);
      loadSuggestions(data.session_id, language);
    } catch (err) {
      setError(err.response?.data?.detail || t.uploadFailed);
    } finally {
      setUploadLoading(false);
    }
  };

  const handleQuery = async (question) => {
    if (!session || queryLoading) return;

    const t = getText(language);
    setError(null);
    setQueryLoading(true);
    topRef.current?.scrollIntoView({ behavior: "smooth" });

    try {
      const data = await runQuery(session.id, question, language);
      setResults((prev) => [{ ...data, question }, ...prev]);
    } catch (err) {
      setError(err.response?.data?.detail || t.queryFailed);
    } finally {
      setQueryLoading(false);
    }
  };

  const handleSuggestionSelect = (suggestion) => {
    setSuggestions((prev) => prev.filter((item) => item !== suggestion));
    handleQuery(suggestion);
  };

  const handleReset = () => {
    setSession(null);
    setResults([]);
    setSuggestions([]);
    setError(null);
  };

  const handleLanguageChange = (nextLanguage) => {
    setLanguage(nextLanguage);
    setResults([]);
    setError(null);
    if (session) loadSuggestions(session.id, nextLanguage);
  };

  if (!session) {
    return (
      <>
        {error && (
          <div className="fixed left-1/2 top-4 z-50 flex max-w-[calc(100vw-2rem)] -translate-x-1/2 items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 shadow-lg">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}
        <UploadZone onUpload={handleUpload} loading={uploadLoading} language={language} />
      </>
    );
  }

  return (
    <AppShell
      session={session}
      status={status}
      language={language}
      onLanguageChange={handleLanguageChange}
      onReset={handleReset}
    >
      <SuggestionChips
        suggestions={suggestions}
        loading={suggestLoading}
        onSelect={handleSuggestionSelect}
        language={language}
      />
      <ResultsPanel
        results={results}
        queryLoading={queryLoading}
        error={error}
        language={language}
        topRef={topRef}
      />
      <QueryComposer onSubmit={handleQuery} loading={queryLoading} language={language} />
    </AppShell>
  );
}
