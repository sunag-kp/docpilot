import { useEffect } from "react";

import type { Citation } from "../api/types";

interface Props {
  citation: Citation | null;
  answer: string;
  onClose: () => void;
}

const normalize = (s: string) => s.toLowerCase().replace(/\s+/g, " ").replace(/[.\s]+$/, "").trim();

/** Show the exact source passage, highlighting the sentences the answer quoted. */
export function CitationDrawer({ citation, answer, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!citation) return null;
  const quoted = new Set(answer.split(/\s*\[\d{1,2}\]\.?\s*/).map(normalize).filter((s) => s.length > 12));
  const sentences = citation.text.split(/(?<=[.!?])\s+/);
  const pages =
    citation.page_start == null
      ? null
      : citation.page_end && citation.page_end !== citation.page_start
        ? `pages ${citation.page_start}–${citation.page_end}`
        : `page ${citation.page_start}`;

  return (
    <div className="fixed inset-0 z-20 flex justify-end bg-slate-900/20" onClick={onClose}>
      <section
        role="dialog"
        aria-label={`Source ${citation.number}`}
        onClick={(e) => e.stopPropagation()}
        className="flex h-full w-full max-w-lg flex-col bg-white shadow-xl"
      >
        <header className="flex items-start justify-between border-b border-slate-200 p-4">
          <div>
            <p className="text-xs font-semibold tracking-wide text-indigo-600 uppercase">Source [{citation.number}]</p>
            <h3 className="mt-0.5 font-semibold">{citation.filename}</h3>
            <p className="text-sm text-slate-600">{[citation.section, pages].filter(Boolean).join(" · ")}</p>
          </div>
          <button onClick={onClose} className="rounded-md px-2 py-1 text-slate-500 hover:bg-slate-100" aria-label="Close">
            ✕
          </button>
        </header>
        <div className="flex-1 overflow-y-auto p-4 text-[15px] leading-relaxed">
          {sentences.map((sentence, i) => (
            <span key={i} className={quoted.has(normalize(sentence)) ? "rounded bg-yellow-100 px-0.5" : undefined}>
              {sentence}{" "}
            </span>
          ))}
        </div>
        <footer className="border-t border-slate-200 p-3 text-xs text-slate-500">
          retrieval score {citation.score.toFixed(4)}
          {citation.vector_similarity != null && ` · cosine similarity ${citation.vector_similarity.toFixed(3)}`}
        </footer>
      </section>
    </div>
  );
}
