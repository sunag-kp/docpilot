# Evaluation harness

A small, fully labeled benchmark for DocPilot's retrieval and answers. Everything runs
locally or in GitHub Actions at zero cost.

* `corpus_src/` — eight fictional Northwind Labs documents written for this repo (no
  licensing issues, impossible to have been memorised by a model).
* `corpus/` — the same documents rendered to **PDF, DOCX and Markdown** by
  `build_corpus.py`, so every parser is exercised.
* `qa.jsonl` — 56 questions: 48 answerable (21 exact-term, 25 paraphrased, 2 numeric /
  multi-fact) and 8 that the corpus cannot answer. Each answerable item lists *evidence*
  (text that identifies the relevant chunk) and *facts* (what a correct answer must say).
* `run_eval.py` — ingests the corpus through the real parser → chunker → embedder → Postgres
  path, then reports:
  * retrieval **hit-rate@k** and **MRR@10** for vector-only, keyword-only and hybrid search;
  * answer **fact recall**, **refusal** accuracy, **citation validity** and latency;
  * **faithfulness** with an LLM judge when `--judge` is set: the harness numbers the
    answer's sentences and the judge labels each one SUPPORTED or UNSUPPORTED by the
    sources (supported / labelled sentences, averaged over answers).

```bash
make infra                                    # Postgres via docker compose
uv run python eval/run_eval.py                # defaults: MiniLM + BM25 + extractive answerer
uv run python eval/run_eval.py --keyword-ranker ts_rank --label ablation-ts-rank
uv run python eval/run_eval.py --embedding hashing --label ablation-hashing-embedder
uv run python eval/run_eval.py --llm ollama --llm-model qwen2.5:3b \
    --judge ollama --judge-model llama3.1:8b  # needs `ollama serve` with both models pulled
```

Results are committed in `results/`:

* `default`, `ablation-*`: local runs of the default configuration and the ablations;
* `ollama-qwen2.5-3b-judge-llama3.1-8b`: generative answers and LLM-judge faithfulness,
  produced by the Evaluation workflow, which publishes every CI run to the `eval-results`
  branch. Its judge labels were checked by hand in the matching `-audit.md`.

Retrieval is deterministic (equal scores are ordered by document and position), so
re-running on the same code reproduces those numbers; answer latency varies with the
machine.

Caveats: the set is small (56 questions), and the extractive answerer's refusal threshold
was chosen during development on this same set (there is no held-out split). Treat the
answer metrics as indicative, not as a benchmark.
