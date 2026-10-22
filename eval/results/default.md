# DocPilot evaluation — 2026-10-22T07:44:45+00:00

Embedding `fastembed:sentence-transformers/all-MiniLM-L6-v2` · keyword ranker `bm25` · answerer `extractive` · judge `none` · 56 questions (48 answerable, 8 unanswerable) · corpus 8 docs / 44 chunks (docx, markdown, pdf)

## Retrieval (answerable questions)

| Mode | hit@1 | hit@3 | hit@5 | hit@10 | MRR@10 |
|---|---|---|---|---|---|
| hybrid | 0.896 | 0.958 | 1.000 | 1.000 | 0.934 |
| vector | 0.917 | 0.979 | 0.979 | 1.000 | 0.944 |
| keyword | 0.812 | 0.896 | 0.938 | 0.979 | 0.867 |

### hit@5 by question type

| Type | n | vector | keyword | hybrid |
|---|---|---|---|---|
| lexical | 21 | 1.000 | 1.000 | 1.000 |
| multi | 1 | 1.000 | 1.000 | 1.000 |
| numeric | 1 | 1.000 | 1.000 | 1.000 |
| paraphrase | 25 | 0.960 | 0.880 | 1.000 |

## Answers (hybrid retrieval)

| Metric | Value |
|---|---|
| Fact recall (answer contains the expected facts) | 0.812 |
| Correct refusals on unanswerable questions | 0.750 |
| False refusals on answerable questions | 0.000 |
| Citation validity (cites only real sources, or refuses) | 1.000 |
| Faithfulness (LLM judge: supported claims / claims) | not measured (no judge) |
| Answer latency p50 / p95 (ms) | 83 / 325 |
