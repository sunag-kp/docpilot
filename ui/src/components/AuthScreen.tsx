import { useState, type FormEvent } from "react";

import type { DocPilotApi, Session } from "../api/types";
import { Logo } from "./Header";

interface Props {
  api: DocPilotApi;
  onSignedIn: (session: Session) => void;
}

export function AuthScreen({ api, onSignedIn }: Props) {
  const [mode, setMode] = useState<"login" | "signup">("signup");
  const [tenant, setTenant] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session =
        mode === "signup" ? await api.signup(tenant, email, password) : await api.login(email, password);
      onSignedIn(session);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  const input =
    "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100";

  return (
    <div className="flex min-h-dvh items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex items-center gap-2.5">
          <Logo />
          <h1 className="text-xl font-semibold">DocPilot</h1>
        </div>
        <p className="text-sm text-slate-600">
          Ask questions about your documents. Answers stream back with citations to the exact passages.
        </p>
        {mode === "signup" && (
          <label className="block space-y-1 text-sm">
            <span className="font-medium">Workspace name</span>
            <input className={input} value={tenant} onChange={(e) => setTenant(e.target.value)} required minLength={2} placeholder="Acme Corp" />
          </label>
        )}
        <label className="block space-y-1 text-sm">
          <span className="font-medium">Email</span>
          <input className={input} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
        </label>
        <label className="block space-y-1 text-sm">
          <span className="font-medium">Password</span>
          <input
            className={input}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            autoComplete={mode === "signup" ? "new-password" : "current-password"}
          />
        </label>
        {error && <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
        <button disabled={busy} className="w-full rounded-lg bg-indigo-600 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-60">
          {busy ? "Please wait…" : mode === "signup" ? "Create workspace" : "Sign in"}
        </button>
        <button
          type="button"
          onClick={() => setMode(mode === "signup" ? "login" : "signup")}
          className="w-full text-center text-sm text-indigo-700 hover:underline"
        >
          {mode === "signup" ? "Already have an account? Sign in" : "New here? Create a workspace"}
        </button>
      </form>
    </div>
  );
}
