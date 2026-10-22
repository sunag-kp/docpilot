import { useEffect, useRef, useState, type FormEvent } from "react";

import { ApiError, type AnswerDone, type Citation, type DocPilotApi } from "../api/types";
import { AnswerText } from "./AnswerText";
import { CitationDrawer } from "./CitationDrawer";

interface Turn {
  id: number;
  question: string;
  answer: string;
  citations: Citation[];
  done: AnswerDone | null;
  error: string | null;
}

interface Props {
  api: DocPilotApi;
  onUnauthorized: () => void;
}

const SUGGESTIONS = [
  "How much paid time off do full-time staff earn each month?",
  "What does error NW-4012 mean?",
  "I lost my work laptop. How quickly must I report it?",
  "What service credit do I get if uptime was 97 percent?",
];

export function ChatPanel({ api, onUnauthorized }: Props) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [open, setOpen] = useState<{ citation: Citation; answer: string } | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }), [turns]);

  const update = (id: number, change: (turn: Turn) => Partial<Turn>) =>
    setTurns((all) => all.map((t) => (t.id === id ? { ...t, ...change(t) } : t)));

  const ask = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || streaming) return;
    const id = Date.now();
    setTurns((all) => [...all, { id, question: trimmed, answer: "", citations: [], done: null, error: null }]);
    setQuestion("");
    setStreaming(true);
    abort.current = new AbortController();
    try {
      await api.ask(
        trimmed,
        {
          onMeta: (meta) => update(id, () => ({ citations: meta.citations })),
          onToken: (token) => update(id, (t) => ({ answer: t.answer + token })),
          onDone: (done) => update(id, () => ({ done, answer: done.answer })),
        },
        abort.current.signal,
      );
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      if (err instanceof ApiError && err.status === 401) onUnauthorized();
      update(id, () => ({ error: err instanceof Error ? err.message : String(err) }));
    } finally {
      setStreaming(false);
      abort.current = null;
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void ask(question);
  };

  return (
    <section className="flex min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6">
        <div className="mx-auto max-w-3xl space-y-6">
          {turns.length === 0 && (
            <div className="space-y-4 pt-8 text-center">
              <h2 className="text-2xl font-semibold tracking-tight">Ask your documents</h2>
              <p className="text-slate-600">Answers cite the exact passages they come from. Click a number to see the source.</p>
              {api.mode === "demo" && (
                <div className="grid gap-2 pt-2 sm:grid-cols-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => void ask(s)}
                      className="rounded-xl border border-slate-200 bg-white p-3 text-left text-sm text-slate-700 hover:border-indigo-300 hover:bg-indigo-50"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {turns.map((turn) => {
            const cited = turn.done ? turn.citations.filter((c) => turn.done!.cited_numbers.includes(c.number)) : [];
            return (
              <article key={turn.id} className="space-y-3">
                <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2 text-white">{turn.question}</p>
                <div className="rounded-2xl rounded-bl-sm border border-slate-200 bg-white px-4 py-3 shadow-sm">
                  {turn.error ? (
                    <p className="text-sm text-red-700">{turn.error}</p>
                  ) : (
                    <p className={`leading-relaxed ${turn.done ? "" : "caret"}`}>
                      <AnswerText
                        text={turn.answer}
                        citations={turn.citations}
                        onCite={(citation) => setOpen({ citation, answer: turn.answer })}
                      />
                    </p>
                  )}
                  {cited.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1.5 border-t border-slate-100 pt-3">
                      {cited.map((c) => (
                        <button
                          key={c.number}
                          onClick={() => setOpen({ citation: c, answer: turn.answer })}
                          className="rounded-lg border border-slate-200 px-2 py-1 text-left text-xs text-slate-600 hover:border-indigo-300 hover:bg-indigo-50"
                        >
                          <span className="font-semibold text-indigo-700">[{c.number}]</span> {c.filename}
                          {c.section && <span className="text-slate-400"> · {c.section.split(" > ").at(-1)}</span>}
                          {c.page_start != null && <span className="text-slate-400"> · p. {c.page_start}</span>}
                        </button>
                      ))}
                    </div>
                  )}
                  {turn.done && (
                    <p className="mt-2 text-xs text-slate-400">
                      {turn.done.cached ? "cached · " : ""}
                      {turn.done.refused ? "no supporting passage found · " : ""}
                      {turn.done.model} · {Math.round(turn.done.latency_ms)} ms
                    </p>
                  )}
                </div>
              </article>
            );
          })}
          <div ref={bottom} />
        </div>
      </div>
      <form onSubmit={submit} className="border-t border-slate-200 bg-white p-3">
        <div className="mx-auto flex max-w-3xl gap-2">
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Ask a question about your documents…"
            maxLength={2000}
            aria-label="Question"
            className="flex-1 rounded-xl border border-slate-300 px-4 py-2.5 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
          />
          {streaming ? (
            <button type="button" onClick={() => abort.current?.abort()} className="rounded-xl border border-slate-300 px-4 text-sm font-medium hover:bg-slate-50">
              Stop
            </button>
          ) : (
            <button disabled={!question.trim()} className="rounded-xl bg-indigo-600 px-5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50">
              Ask
            </button>
          )}
        </div>
      </form>
      <CitationDrawer citation={open?.citation ?? null} answer={open?.answer ?? ""} onClose={() => setOpen(null)} />
    </section>
  );
}
