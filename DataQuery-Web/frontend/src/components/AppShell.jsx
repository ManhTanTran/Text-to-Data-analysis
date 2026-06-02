import { useState } from "react";
import SchemaDrawer from "./SchemaDrawer";
import SchemaPanel from "./SchemaPanel";
import StatusBanner from "./StatusBanner";
import TopBar from "./TopBar";

export default function AppShell({
  session,
  status,
  language,
  onLanguageChange,
  onReset,
  children,
}) {
  const [schemaOpen, setSchemaOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden bg-slate-50 text-slate-900">
      <aside className="hidden h-full w-72 shrink-0 border-r border-slate-200 lg:block">
        <SchemaPanel
          session={session}
          onReset={onReset}
          language={language}
          onLanguageChange={onLanguageChange}
        />
      </aside>

      <SchemaDrawer
        open={schemaOpen}
        onClose={() => setSchemaOpen(false)}
        session={session}
        onReset={onReset}
        language={language}
        onLanguageChange={onLanguageChange}
      />

      <main className="flex min-w-0 flex-1 flex-col">
        <TopBar
          session={session}
          status={status}
          language={language}
          onOpenSchema={() => setSchemaOpen(true)}
        />
        <StatusBanner status={status} language={language} />
        {children}
      </main>
    </div>
  );
}
