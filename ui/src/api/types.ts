export type DocumentStatus = "queued" | "processing" | "ready" | "failed";

export interface DocumentItem {
  id: string;
  filename: string;
  kind: string;
  status: DocumentStatus;
  size_bytes: number;
  chunk_count: number | null;
  page_count: number | null;
  error: string | null;
  created_at: string;
}

export interface Citation {
  number: number;
  chunk_id: string;
  document_id: string;
  filename: string;
  section: string | null;
  page_start: number | null;
  page_end: number | null;
  text: string;
  score: number;
  vector_similarity: number | null;
}

export interface AnswerMeta {
  query_id: string;
  cached: boolean;
  model: string;
  citations: Citation[];
}

export interface AnswerDone {
  query_id: string;
  answer: string;
  cited_numbers: number[];
  refused: boolean;
  grounded: boolean;
  cached: boolean;
  model: string;
  latency_ms: number;
}

export interface Session {
  token: string;
  tenantId: string;
  email: string;
}

export interface StreamHandlers {
  onMeta: (meta: AnswerMeta) => void;
  onToken: (text: string) => void;
  onDone: (done: AnswerDone) => void;
}

/** Everything the UI needs from a backend: the real API or the in-browser demo. */
export interface DocPilotApi {
  readonly mode: "live" | "demo";
  signup(tenantName: string, email: string, password: string): Promise<Session>;
  login(email: string, password: string): Promise<Session>;
  setSession(session: Session | null): void;
  listDocuments(): Promise<DocumentItem[]>;
  upload(file: File): Promise<DocumentItem>;
  deleteDocument(id: string): Promise<void>;
  ask(question: string, handlers: StreamHandlers, signal?: AbortSignal): Promise<void>;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}
