---
title: DocPilot
emoji: 📚
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Document Q&A with streamed, cited answers
---

# DocPilot

Upload PDF, DOCX or Markdown files, ask questions, and get streamed answers that cite the
exact passages they came from. Sign up with any email address to get your own private
workspace.

This Space runs the real application in one container: the FastAPI service with its
ingestion worker, Postgres with pgvector, Redis and the React UI. Answers come from the
built-in keyless answerer, which quotes and cites the most relevant sentences. Uploaded
data is cleared whenever the Space restarts.

Source code, architecture and benchmarks: https://github.com/sunag-kp/docpilot
