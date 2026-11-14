import { afterEach, describe, expect, it, vi } from "vitest";

import { HttpApi } from "./http";
import type { AnswerDone, AnswerMeta } from "./types";

const answer = {
  query_id: "q1",
  answer: "Employees get 20 days [1].",
  citations: [],
  cited_numbers: [1],
  refused: false,
  grounded: true,
  cached: false,
  model: "extractive",
  latency_ms: 12,
};

function collect() {
  const seen = { meta: [] as AnswerMeta[], tokens: [] as string[], done: [] as AnswerDone[] };
  const handlers = {
    onMeta: (m: AnswerMeta) => seen.meta.push(m),
    onToken: (t: string) => seen.tokens.push(t),
    onDone: (d: AnswerDone) => seen.done.push(d),
  };
  return { seen, handlers };
}

afterEach(() => vi.unstubAllGlobals());

describe("HttpApi.ask", () => {
  it("falls back to the JSON endpoint when the stream ends without a done event", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("event: meta\ndata: {}\n\n", { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(answer), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { seen, handlers } = collect();

    await new HttpApi("").ask("How much PTO?", handlers);

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual(["/v1/query/stream", "/v1/query"]);
    expect(seen.done.at(-1)?.answer).toBe(answer.answer);
  });

  it("does not retry when the stream completes", async () => {
    const stream = `event: token\ndata: {"text":"hi"}\n\nevent: done\ndata: ${JSON.stringify(answer)}\n\n`;
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(stream, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { seen, handlers } = collect();

    await new HttpApi("").ask("How much PTO?", handlers);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(seen.tokens).toEqual(["hi"]);
  });

  it("surfaces API errors instead of retrying", async () => {
    const problem = { title: "Unavailable", status: 503, detail: "model unavailable" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(problem), { status: 503 })));
    await expect(new HttpApi("").ask("q", collect().handlers)).rejects.toThrow("model unavailable");
  });
});
