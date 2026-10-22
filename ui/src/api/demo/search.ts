/**
 * Browser-side retrieval for the demo build: heading-aware chunking, BM25 ranking and an
 * extractive, cited answer. A simplified stand-in for the real backend (which runs hybrid
 * pgvector + BM25 search in Postgres), so the GitHub Pages demo needs no server at all.
 */

export interface DemoChunk {
  id: string;
  documentId: string;
  filename: string;
  section: string | null;
  text: string;
}

const STOPWORDS = new Set(
  `a about above after again against all am an and any are as at be because been before being
  below between both but by can could did do does doing down during each few for from further
  had has have having he her here hers him his how i if in into is it its just me more most my
  no nor not now of off on once only or other our out over own same she should so some such
  than that the their them then there these they this those through to too under until up very
  was we were what when where which while who whom why will with would you your get many much`.split(/\s+/),
);

export function terms(text: string): string[] {
  return (text.toLowerCase().match(/[a-z0-9]+(?:-[a-z0-9]+)*/g) ?? [])
    .filter((word) => word.length > 1 && !STOPWORDS.has(word))
    .map(stem);
}

function stem(word: string): string {
  for (const [suffix, replacement] of [
    ["ies", "y"],
    ["ing", ""],
    ["ed", ""],
    ["es", ""],
    ["s", ""],
  ] as const) {
    if (word.endsWith(suffix) && word.length - suffix.length >= 3) {
      return word.slice(0, word.length - suffix.length) + replacement;
    }
  }
  return word;
}

export function splitSentences(text: string): string[] {
  return text
    .replace(/\s+/g, " ")
    .split(/(?<=[.!?])\s+(?=[A-Z0-9"'(])/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** One chunk per section: headings always start a new chunk, like the real chunker. */
export function chunkMarkdown(documentId: string, filename: string, markdown: string): DemoChunk[] {
  const chunks: DemoChunk[] = [];
  let title: string | null = null;
  let section: string | null = null;
  let body: string[] = [];
  const flush = () => {
    const text = body.join(" ").replace(/\s+/g, " ").trim();
    if (text) {
      const path = [title, section].filter(Boolean).join(" > ") || null;
      chunks.push({ id: `${documentId}:${chunks.length}`, documentId, filename, section: path, text });
    }
    body = [];
  };
  for (const line of markdown.split("\n")) {
    const heading = /^(#{1,6})\s+(.*)$/.exec(line.trim());
    if (heading) {
      flush();
      if (heading[1]!.length === 1) title = heading[2]!.trim();
      else section = heading[2]!.trim();
    } else if (line.trim() === "") {
      body.push("\n");
    } else {
      body.push(line.trim());
    }
  }
  flush();
  return chunks;
}

export interface Hit {
  chunk: DemoChunk;
  score: number;
}

/** Okapi BM25 (k1 = 1.2, b = 0.75) over the section path + text of every chunk. */
export function bm25(chunks: DemoChunk[], query: string, limit = 5): Hit[] {
  const queryTerms = [...new Set(terms(query))];
  if (!queryTerms.length || !chunks.length) return [];
  const docs = chunks.map((chunk) => terms(`${chunk.section ?? ""} ${chunk.text}`));
  const avgLength = docs.reduce((sum, d) => sum + d.length, 0) / docs.length;
  const df = new Map<string, number>();
  for (const doc of docs) for (const term of new Set(doc)) df.set(term, (df.get(term) ?? 0) + 1);
  const hits: Hit[] = [];
  docs.forEach((doc, i) => {
    let score = 0;
    for (const term of queryTerms) {
      const tf = doc.filter((t) => t === term).length;
      if (!tf) continue;
      const n = df.get(term) ?? 0;
      const idf = Math.log(1 + (docs.length - n + 0.5) / (n + 0.5));
      score += (idf * tf * 2.2) / (tf + 1.2 * (0.25 + (0.75 * doc.length) / avgLength));
    }
    if (score > 0) hits.push({ chunk: chunks[i]!, score });
  });
  return hits.sort((a, b) => b.score - a.score).slice(0, limit);
}

export const REFUSAL = "I don't know based on the provided documents.";

/** Pick the source sentences sharing the most (IDF-weighted) information with the question. */
export function extractiveAnswer(question: string, sources: DemoChunk[], minOverlap = 0.34): string {
  const questionTerms = new Set(terms(question));
  const rows = sources.flatMap((source, rank) =>
    splitSentences(source.text).map((sentence, position) => ({
      number: rank + 1,
      rank,
      position,
      sentence,
      terms: new Set(terms(sentence)),
    })),
  );
  if (!rows.length || !questionTerms.size) return REFUSAL;
  const df = new Map<string, number>();
  for (const row of rows) for (const t of row.terms) df.set(t, (df.get(t) ?? 0) + 1);
  const idf = (t: string) => Math.log(1 + rows.length / (1 + (df.get(t) ?? 0)));
  const mass = [...questionTerms].reduce((sum, t) => sum + idf(t), 0) || 1;
  const scored = rows
    .map((row) => ({
      ...row,
      overlap: [...questionTerms].filter((t) => row.terms.has(t)).reduce((s, t) => s + idf(t), 0) / mass,
    }))
    .sort((a, b) => b.overlap - a.overlap || a.rank - b.rank);
  const best = scored[0]!;
  if (best.overlap < minOverlap) return REFUSAL;
  const chosen = scored.filter((r) => r.overlap >= best.overlap * 0.75).slice(0, 2);
  chosen.sort((a, b) => a.rank - b.rank || a.position - b.position);
  return chosen.map((r) => `${r.sentence.replace(/\.$/, "")} [${r.number}].`).join(" ");
}
