# DocPilot evaluation — 2026-10-22T11:50:55+00:00

Embedding `hashing` · keyword ranker `bm25` · answerer `extractive` · judge `none` · 56 questions (48 answerable, 8 unanswerable) · corpus 8 docs / 44 chunks (docx, markdown, pdf)

## Retrieval (answerable questions)

| Mode | hit@1 | hit@3 | hit@5 | hit@10 | MRR@10 |
|---|---|---|---|---|---|
| hybrid | 0.771 | 0.875 | 0.896 | 0.958 | 0.836 |
| vector | 0.708 | 0.729 | 0.729 | 0.729 | 0.719 |
| keyword | 0.812 | 0.896 | 0.938 | 0.958 | 0.862 |

### hit@5 by question type

| Type | n | vector | keyword | hybrid |
|---|---|---|---|---|
| lexical | 21 | 0.952 | 1.000 | 0.952 |
| multi | 1 | 1.000 | 1.000 | 1.000 |
| numeric | 1 | 1.000 | 1.000 | 1.000 |
| paraphrase | 25 | 0.520 | 0.880 | 0.840 |

## Answers (hybrid retrieval)

| Metric | Value |
|---|---|
| Fact recall (answer contains the expected facts) | 0.250 |
| Correct refusals on unanswerable questions | 1.000 |
| False refusals on answerable questions | 0.708 |
| Citation validity (cites only real sources, or refuses) | 1.000 |
| Faithfulness (LLM judge: supported claims / claims) | not measured (no judge) |
| Answer latency p50 / p95 (ms) | 9 / 13 |
