import type { Citation } from "../api/types";

interface Props {
  text: string;
  citations: Citation[];
  onCite: (citation: Citation) => void;
}

/** Render "... [2] ..." with each marker as a button that opens the cited passage. */
export function AnswerText({ text, citations, onCite }: Props) {
  const parts = text.split(/(\[\d{1,2}\])/g);
  return (
    <>
      {parts.map((part, i) => {
        const marker = /^\[(\d{1,2})\]$/.exec(part);
        const citation = marker ? citations.find((c) => c.number === Number(marker[1])) : undefined;
        if (!citation) return <span key={i}>{part}</span>;
        return (
          <button
            key={i}
            onClick={() => onCite(citation)}
            title={`${citation.filename}${citation.section ? ` — ${citation.section}` : ""}`}
            className="mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-md bg-indigo-100 px-1 align-text-top text-xs font-semibold text-indigo-700 hover:bg-indigo-600 hover:text-white"
          >
            {citation.number}
          </button>
        );
      })}
    </>
  );
}
