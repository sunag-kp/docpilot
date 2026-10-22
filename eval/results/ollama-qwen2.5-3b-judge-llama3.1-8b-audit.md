# Hand audit of the LLM judge — `ollama-qwen2.5-3b-judge-llama3.1-8b`

Run: Evaluation workflow on commit `58b4eee` (GitHub Actions, CPU-only runner), copied
from the `eval-results` branch (`runs/58b4eee/`). Generator `qwen2.5:3b`, judge
`llama3.1:8b`, both served by Ollama.

The judge labelled 50 sentences from the 46 non-refused answers: 45 SUPPORTED and 5
UNSUPPORTED, for a faithfulness of 0.902 (mean per answer). Every label was checked by
hand against the source documents in `eval/corpus_src/`.

## The 5 sentences the judge called UNSUPPORTED

| Question id | Sentence | Source says | Judge correct? |
|---|---|---|---|
| `pto-notice` | Requests for more than five consecutive days must be submitted at least three weeks in advance. | Same sentence, verbatim (employee handbook). | No |
| `doctor-note` | You need to show a medical certificate to HR if you are sick for more than three consecutive working days. | "…you must provide a doctor's note to HR." A faithful paraphrase. | No |
| `after-cancel` | When you stop paying and close your account, your data is kept for 30 days so that you can reactivate or export it, after which it is permanently deleted. | "After cancellation your data is kept for 30 days so that you can reactivate or export it, and then it is permanently deleted." | No |
| `home-office` | Yes, the company will pay for your desk and chair at home. | A one-time 400-euro stipend for new remote workers, not open-ended payment. | Yes |
| `u-eu-cloud` (unanswerable) | The Frankfurt data center is run by. | The corpus does not say. The generator should have refused. | Yes |

## The 45 sentences the judge called SUPPORTED

44 are stated by the sources. One is borderline: `status-page` says the status page "must
be updated within 15 minutes … and then at least every 30 minutes", but the source scopes
that rule to SEV1 incidents.

## Reading

- The judge agrees with the hand labels on 46 or 47 of 50 sentences.
- Its mistakes are mostly false alarms on verbatim or near-verbatim sentences, so it errs
  on the strict side.
- By hand, 47 to 48 of the 50 generated sentences are supported by the sources.
- This is one run of a small local judge on a small set. Treat 0.902 as a conservative
  estimate, not a precise one.
