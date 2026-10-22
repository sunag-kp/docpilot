import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";

import { ApiError, type DocPilotApi, type DocumentItem } from "../api/types";
import { StatusBadge } from "./StatusBadge";

interface Props {
  api: DocPilotApi;
  onUnauthorized: () => void;
}

const ACCEPT = ".pdf,.docx,.md,.markdown,.txt";

function formatBytes(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

export function DocumentsPanel({ api, onUnauthorized }: Props) {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      setDocuments(await api.listDocuments());
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized();
      else setError(err instanceof Error ? err.message : String(err));
    }
  }, [api, onUnauthorized]);

  // Poll while anything is still being ingested; the worker updates status asynchronously.
  const pending = documents.some((d) => d.status === "queued" || d.status === "processing");
  useEffect(() => {
    const first = setTimeout(() => void refresh(), 0);
    const timer = setInterval(() => void refresh(), pending ? 1000 : 10000);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh, pending]);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setError(null);
    for (const file of Array.from(files)) {
      try {
        await api.upload(file);
      } catch (err) {
        setError(`${file.name}: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
    await refresh();
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    void upload(event.dataTransfer.files);
  };

  const remove = async (doc: DocumentItem) => {
    await api.deleteDocument(doc.id);
    await refresh();
  };

  return (
    <aside className="flex min-h-0 flex-col border-b border-slate-200 bg-white lg:border-r lg:border-b-0">
      <div className="p-4">
        <h2 className="mb-3 text-sm font-semibold text-slate-700">Documents</h2>
        <button
          type="button"
          onClick={() => input.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`flex w-full flex-col items-center gap-1 rounded-xl border-2 border-dashed px-4 py-5 text-sm transition ${
            dragging ? "border-indigo-500 bg-indigo-50" : "border-slate-300 hover:border-indigo-400 hover:bg-slate-50"
          }`}
        >
          <span className="font-medium text-slate-800">Drop files or click to upload</span>
          <span className="text-xs text-slate-500">PDF, DOCX, Markdown or text</span>
        </button>
        <input ref={input} type="file" multiple accept={ACCEPT} hidden onChange={(e) => void upload(e.target.files)} />
        {error && <p role="alert" className="mt-2 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">{error}</p>}
      </div>
      <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2 pb-4" aria-label="Documents">
        {documents.length === 0 && <li className="px-2 text-sm text-slate-500">No documents yet.</li>}
        {documents.map((doc) => (
          <li key={doc.id} className="group rounded-lg px-2 py-2 hover:bg-slate-50">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-sm font-medium" title={doc.filename}>{doc.filename}</span>
              <StatusBadge status={doc.status} />
            </div>
            <div className="mt-0.5 flex items-center justify-between text-xs text-slate-500">
              <span>
                {doc.kind} · {formatBytes(doc.size_bytes)}
                {doc.chunk_count != null && ` · ${doc.chunk_count} chunks`}
                {doc.page_count != null && ` · ${doc.page_count} page${doc.page_count === 1 ? "" : "s"}`}
              </span>
              <button
                onClick={() => void remove(doc)}
                className="invisible text-slate-400 group-hover:visible hover:text-red-600"
                aria-label={`Delete ${doc.filename}`}
              >
                Delete
              </button>
            </div>
            {doc.error && <p className="mt-1 text-xs text-red-600">{doc.error}</p>}
          </li>
        ))}
      </ul>
    </aside>
  );
}
