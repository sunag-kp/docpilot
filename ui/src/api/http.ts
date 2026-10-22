import { readSse } from "./sse";
import {
  ApiError,
  type AnswerDone,
  type AnswerMeta,
  type DocPilotApi,
  type DocumentItem,
  type Session,
  type StreamHandlers,
} from "./types";

interface Problem {
  detail?: string;
  errors?: { loc: string[]; msg: string }[];
}

async function failure(response: Response): Promise<ApiError> {
  let message = `${response.status} ${response.statusText}`;
  try {
    const problem = (await response.json()) as Problem;
    const first = problem.errors?.[0];
    message = first ? `${first.loc.at(-1)}: ${first.msg}` : (problem.detail ?? message);
  } catch {
    // not JSON: keep the status line
  }
  if (response.status === 429) {
    const retry = response.headers.get("Retry-After");
    message = `Rate limit reached${retry ? ` — try again in ${retry}s` : ""}.`;
  }
  return new ApiError(response.status, message);
}

/** Talks to the real FastAPI backend (same origin, or VITE_API_BASE). */
export class HttpApi implements DocPilotApi {
  readonly mode = "live" as const;
  private session: Session | null = null;
  private readonly base: string;

  constructor(base: string = import.meta.env.VITE_API_BASE ?? "") {
    this.base = base.replace(/\/$/, "");
  }

  setSession(session: Session | null): void {
    this.session = session;
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    return this.session ? { ...extra, Authorization: `Bearer ${this.session.token}` } : extra;
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${this.base}${path}`, init);
    if (!response.ok) throw await failure(response);
    return (response.status === 204 ? undefined : await response.json()) as T;
  }

  private async authenticate(path: string, body: object, email: string): Promise<Session> {
    const token = await this.request<{ access_token: string; tenant_id: string }>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return { token: token.access_token, tenantId: token.tenant_id, email };
  }

  signup(tenantName: string, email: string, password: string): Promise<Session> {
    return this.authenticate("/v1/auth/signup", { tenant_name: tenantName, email, password }, email);
  }

  login(email: string, password: string): Promise<Session> {
    return this.authenticate("/v1/auth/login", { email, password }, email);
  }

  async listDocuments(): Promise<DocumentItem[]> {
    const page = await this.request<{ items: DocumentItem[] }>("/v1/documents?limit=200", {
      headers: this.headers(),
    });
    return page.items;
  }

  upload(file: File): Promise<DocumentItem> {
    const form = new FormData();
    form.append("file", file);
    return this.request<DocumentItem>("/v1/documents", {
      method: "POST",
      headers: this.headers(),
      body: form,
    });
  }

  deleteDocument(id: string): Promise<void> {
    return this.request<void>(`/v1/documents/${id}`, { method: "DELETE", headers: this.headers() });
  }

  async ask(question: string, handlers: StreamHandlers, signal?: AbortSignal): Promise<void> {
    const response = await fetch(`${this.base}/v1/query/stream`, {
      method: "POST",
      headers: this.headers({ "Content-Type": "application/json", Accept: "text/event-stream" }),
      body: JSON.stringify({ question }),
      signal,
    });
    if (!response.ok || !response.body) throw await failure(response);
    for await (const event of readSse(response.body)) {
      const data: unknown = JSON.parse(event.data);
      if (event.event === "meta") handlers.onMeta(data as AnswerMeta);
      else if (event.event === "token") handlers.onToken((data as { text: string }).text);
      else if (event.event === "done") handlers.onDone(data as AnswerDone);
      else if (event.event === "error") throw new ApiError(503, (data as { detail: string }).detail);
    }
  }
}
