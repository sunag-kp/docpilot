# DocPilot demo script (about 5 minutes)

## Before the demo (20 minutes ahead)

1. **Start the full app.** Open https://codespaces.new/sunag-kp/docpilot?quickstart=1. The
   Codespace runs `make up-ai`:
   - it starts the full stack and a free local AI model (Ollama, `qwen2.5:3b`);
   - the first start takes 10–15 minutes, because it builds the images and downloads the
     ~2 GB model;
   - when it's ready, the UI opens on port 8000 (PORTS tab → globe icon).
2. **Download the sample documents** from
   [`eval/corpus`](https://github.com/sunag-kp/docpilot/tree/main/eval/corpus):
   `employee-handbook.md`, `service-level-agreement.pdf`, `product-faq.docx` and
   `security-policy.docx`. Add a resume PDF too if you like.
3. **Do a dry run.** Sign up, upload the files and ask one question, so the model is already
   loaded in memory. The first answer after a cold start is the slowest.
4. **Keep a backup open:** https://sunag-kp.github.io/docpilot/ runs entirely in the browser
   and always loads instantly.

## On stage

| Step | Do | Point out |
|---|---|---|
| 1 | Sign up with a workspace name | Each workspace is a separate tenant; its documents are invisible to others |
| 2 | Upload the four files (PDF, DOCX, Markdown) at once | Upload returns immediately; workers parse, chunk, embed and index in the background via Kafka (queued → processing → ready) |
| 3 | Ask *How many days of paid time off do full-time employees get per month?* | The answer streams in with a **[1]** citation |
| 4 | Click **[1]** | The exact source passage, file and section open, with the cited sentence highlighted |
| 5 | Ask *What uptime does the Enterprise plan guarantee, and what credit do I get if it is missed?* | The answer combines facts from two sections of a PDF, each cited |
| 6 | Ask *What does error code NW-4012 mean?* | Exact identifiers are found by keyword (BM25) search, while paraphrases are found by vector search; the two are fused with RRF |
| 7 | Ask *Who is the CEO of Northwind?* | No document says, so it answers **"I don't know based on the provided documents"** instead of guessing |
| 8 | With a resume uploaded, ask *Summarize this candidate's strengths for a backend role* | The local model writes the answer and cites the resume |
| 9 | Optional: open port 3000 (Grafana) | Live request rates, latency per stage and ingestion throughput |

## Talking points

- **Free and local.** The embeddings come from a local ONNX model, the answers from an
  open-weights model running on CPU. There are no API keys and no paid services.
- **Reliable ingestion.**
  - Kafka with at-least-once delivery and idempotent workers (lease claims, sha256
    de-duplication);
  - exponential backoff with jitter, and a dead-letter topic.
- **Retrieval in Postgres.** pgvector HNSW plus BM25 computed in SQL, fused with Reciprocal
  Rank Fusion. Hybrid search found the right passage in the top 5 for all 48 answerable
  evaluation questions.
- **Measured, not claimed.**
  - Faithfulness of the AI answers: 0.902, scored by an AI judge and checked by hand.
  - 426 documents per minute ingested.
  - p50 38 ms per query with one user.
  - All numbers are committed with the commands that produced them.

## If something goes wrong

- **AI answers are slow.** It's CPU-only, so 10–30 seconds per answer is normal. Without
  the model, `make up` gives instant answers that quote the most relevant sentences.
- **Port 8000 is missing.** Open a terminal in the Codespace and run `make up-ai`.
- **Anything else.** Switch to the browser demo:
  https://sunag-kp.github.io/docpilot/
