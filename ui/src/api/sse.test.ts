import { describe, expect, it } from "vitest";

import { SseParser, readSse } from "./sse";

describe("SseParser", () => {
  it("parses events split across arbitrary chunk boundaries", () => {
    const parser = new SseParser();
    expect(parser.push('event: meta\ndata: {"a"')).toEqual([]);
    expect(parser.push(':1}\n\nevent: token\r\ndata: {"text":"hi"}\r\n\r\n')).toEqual([
      { event: "meta", data: '{"a":1}' },
      { event: "token", data: '{"text":"hi"}' },
    ]);
  });

  it("ignores keep-alive comments and joins multi-line data", () => {
    const parser = new SseParser();
    expect(parser.push(": keep-alive\n\ndata: line1\ndata: line2\n\n")).toEqual([
      { event: "message", data: "line1\nline2" },
    ]);
  });
});

describe("readSse", () => {
  it("decodes a byte stream into events", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode("event: token\ndata: {\"text\":\"Hé"));
        controller.enqueue(encoder.encode('llo"}\n\nevent: done\ndata: {}\n\n'));
        controller.close();
      },
    });
    const events = [];
    for await (const event of readSse(body)) events.push(event);
    expect(events.map((e) => e.event)).toEqual(["token", "done"]);
    expect(JSON.parse(events[0]!.data)).toEqual({ text: "Héllo" });
  });
});
