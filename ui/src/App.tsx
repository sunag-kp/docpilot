import { useMemo, useState } from "react";

import { DemoApi } from "./api/demo/demoApi";
import { HttpApi } from "./api/http";
import type { DocPilotApi, Session } from "./api/types";
import { AuthScreen } from "./components/AuthScreen";
import { ChatPanel } from "./components/ChatPanel";
import { DocumentsPanel } from "./components/DocumentsPanel";
import { Header } from "./components/Header";

const SESSION_KEY = "docpilot.session";

function createApi(): DocPilotApi {
  return import.meta.env.VITE_DEMO === "true" ? new DemoApi() : new HttpApi();
}

function restoreSession(api: DocPilotApi): Session | null {
  if (api.mode === "demo") return { token: "demo", tenantId: "demo-tenant", email: "you@demo" };
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export default function App() {
  const api = useMemo(() => createApi(), []);
  const [session, setSession] = useState<Session | null>(() => {
    const restored = restoreSession(api);
    api.setSession(restored);
    return restored;
  });

  const signIn = (next: Session | null) => {
    api.setSession(next);
    setSession(next);
    try {
      if (next && api.mode === "live") localStorage.setItem(SESSION_KEY, JSON.stringify(next));
      else localStorage.removeItem(SESSION_KEY);
    } catch {
      // storage unavailable (private mode): the session just won't survive a reload
    }
  };

  if (!session) return <AuthScreen api={api} onSignedIn={signIn} />;

  return (
    <div className="flex h-dvh flex-col">
      <Header mode={api.mode} email={session.email} onSignOut={() => signIn(null)} />
      {api.mode === "demo" && (
        <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-center text-sm text-amber-900">
          Demo mode: everything runs in your browser against a mock backend with keyword search.
          The real system adds Kafka ingestion, pgvector hybrid search and PDF/DOCX parsing —{" "}
          <a className="font-medium underline" href="https://github.com/sunag-kp/docpilot#quick-start">
            run it with <code>make up</code>
          </a>
          .
        </div>
      )}
      <main className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[22rem_1fr]">
        <DocumentsPanel api={api} onUnauthorized={() => signIn(null)} />
        <ChatPanel api={api} onUnauthorized={() => signIn(null)} />
      </main>
    </div>
  );
}
