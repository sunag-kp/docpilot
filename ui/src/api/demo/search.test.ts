import { describe, expect, it } from "vitest";

import { bm25, chunkMarkdown, extractiveAnswer, REFUSAL, terms } from "./search";

const HANDBOOK = `# Handbook

## Vacation
Full-time employees accrue 1.75 days of paid time off per month. Requests need approval.

## Remote work
Employees may work remotely up to three days per week.

## Kitchen
The kitchen is cleaned on Fridays.`;

describe("demo search", () => {
  const chunks = chunkMarkdown("d1", "handbook.md", HANDBOOK);

  it("chunks by heading with section paths", () => {
    expect(chunks.map((c) => c.section)).toEqual([
      "Handbook > Vacation",
      "Handbook > Remote work",
      "Handbook > Kitchen",
    ]);
  });

  it("drops stop words and stems", () => {
    expect(terms("How many days do employees get?")).toEqual(["day", "employe"]);
  });

  it("ranks the matching section first", () => {
    const hits = bm25(chunks, "How many days can I work remotely?");
    expect(hits[0]!.chunk.section).toBe("Handbook > Remote work");
    expect(bm25(chunks, "the and of")).toEqual([]);
  });

  it("answers with citations or refuses", () => {
    const answer = extractiveAnswer("How much paid time off accrues per month?", [chunks[0]!]);
    expect(answer).toBe("Full-time employees accrue 1.75 days of paid time off per month [1].");
    expect(extractiveAnswer("What is the capital of France?", [chunks[2]!])).toBe(REFUSAL);
  });
});
