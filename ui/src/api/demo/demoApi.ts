import { bm25, chunkMarkdown, extractiveAnswer, REFUSAL, type DemoChunk } from "./search";
import type { Citation, DocPilotApi, DocumentItem, Session, StreamHandlers } from "../types";

// The evaluation corpus doubles as the demo's preloaded documents.
const corpus = import.meta.glob("../../../../eval/corpus_src/*.md", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function newId(): string {
  return globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);
}

/**
 * The whole backend, in the browser, for the GitHub Pages demo: documents, simulated
 * asynchronous ingestion, BM25 retrieval and streamed extractive answers with citations.
 */
export class DemoApi implements DocPilotApi {
  readonly mode = "demo" as const;
  private documents: DocumentItem[] = [];
  private chunks: DemoChunk[] = [];

  constructor() {
    for (const [path, markdown] of Object.entries(corpus)) {
      const filename = path.split("/").pop()!;
      const id = newId();
      this.chunks.push(...chunkMarkdown(id, filename, markdown));
      this.documents.push(this.item(id, filename, markdown.length, "ready"));
    }
    this.refreshCounts();
  }

  private item(id: string, filename: string, size: number, status: DocumentItem["status"]): DocumentItem {
    return {
      id,
      filename,
      kind: filename.endsWith(".pdf") ? "pdf" : filename.endsWith(".docx") ? "docx" : "markdown",
      status,
      size_bytes: size,
      chunk_count: null,
      page_count: null,
      error: null,
      created_at: new Date().toISOString(),
    };
  }

  private refreshCounts(): void {
    for (const doc of this.documents) {
      if (doc.status === "ready") doc.chunk_count = this.chunks.filter((c) => c.documentId === doc.id).length;
    }
  }

  setSession(): void {}

  async signup(_tenant: string, email: string): Promise<Session> {
    return { token: "demo", tenantId: "demo-tenant", email };
  }

  async login(email: string): Promise<Session> {
    return { token: "demo", tenantId: "demo-tenant", email };
  }

  async listDocuments(): Promise<DocumentItem[]> {
    return this.documents.map((d) => ({ ...d }));
  }

  async upload(file: File): Promise<DocumentItem> {
    const doc = this.item(newId(), file.name, file.size, "queued");
    this.documents.unshift(doc);
    void this.ingest(doc, file);
    return { ...doc };
  }

  private async ingest(doc: DocumentItem, file: File): Promise<void> {
    await sleep(600);
    doc.status = "processing";
    await sleep(900);
    if (!/\.(md|markdown|txt)$/i.test(file.name)) {
      doc.status = "failed";
      doc.error = "The browser demo parses Markdown and text only; PDF/DOCX parsing runs in the real backend (make up).";
      return;
    }
    this.chunks.push(...chunkMarkdown(doc.id, doc.filename, await file.text()));
    doc.status = "ready";
    this.refreshCounts();
  }

  async deleteDocument(id: string): Promise<void> {
    this.documents = this.documents.filter((d) => d.id !== id);
    this.chunks = this.chunks.filter((c) => c.documentId !== id);
  }

  async ask(question: string, handlers: StreamHandlers, signal?: AbortSignal): Promise<void> {
    const started = performance.now();
    const hits = bm25(this.chunks, question, 5);
    const citations: Citation[] = hits.map((hit, i) => ({
      number: i + 1,
      chunk_id: hit.chunk.id,
      document_id: hit.chunk.documentId,
      filename: hit.chunk.filename,
      section: hit.chunk.section,
      page_start: null,
      page_end: null,
      text: hit.chunk.text,
      score: Math.round(hit.score * 1000) / 1000,
      vector_similarity: null,
    }));
    const queryId = newId();
    handlers.onMeta({ query_id: queryId, cached: false, model: "demo-extractive", citations });
    const answer = citations.length ? extractiveAnswer(question, hits.map((h) => h.chunk)) : REFUSAL;
    const words = answer.split(" ");
    for (const [i, word] of words.entries()) {
      if (signal?.aborted) return;
      await sleep(18);
      handlers.onToken(i === words.length - 1 ? word : `${word} `);
    }
    const cited = [...new Set([...answer.matchAll(/\[(\d+)\]/g)].map((m) => Number(m[1])))];
    handlers.onDone({
      query_id: queryId,
      answer,
      cited_numbers: cited,
      refused: answer === REFUSAL,
      grounded: answer === REFUSAL || cited.length > 0,
      cached: false,
      model: "demo-extractive",
      latency_ms: Math.round(performance.now() - started),
    });
  }
}
