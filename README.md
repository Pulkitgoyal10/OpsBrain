# OpsBrain

**An industrial knowledge-intelligence platform that turns scattered equipment manuals, maintenance reports, safety data sheets, and inspection logs into cited, plain-English answers.**

Ask a question and get a grounded answer with a citation back to the exact document and page it came from. A Neo4j knowledge graph runs alongside vector search, so relational questions ("what regulations apply to what this equipment handles?") are answered by deterministic graph traversal instead of similarity-search guesswork — and a compliance agent flags overdue maintenance directly from the graph.

<p>
  <a href="https://opsbrain-eight.vercel.app/">
    <img src="https://img.shields.io/badge/Live%20Demo-View%20App-brightgreen?style=for-the-badge" alt="Live Demo" />
  </a>
  <a href="https://github.com/Pulkitgoyal10/OpsBrain">
    <img src="https://img.shields.io/badge/GitHub-Repository-181717?style=for-the-badge&logo=github&logoColor=white" alt="GitHub Repo" />
  </a>
</p>

---

## Overview

Most maintenance and compliance knowledge in industrial operations sits locked inside PDFs, spreadsheets, CAD drawings, and scanned inspection logs. OpsBrain ingests all of it, extracts structured entities and relationships into a knowledge graph, and lets you query it in natural language — with every answer traceable back to its source.

## Features

- **Multi-format ingestion** — PDF, CSV, XLSX, DXF (CAD drawings), and standalone PNG/JPG/JPEG images, with live per-file upload status and toast notifications on success/failure.
- **Chat with citations** — every claim in an answer is cited back to its source document and page; scope a question to a single document or the whole corpus.
- **Knowledge graph explorer** — an interactive, force-directed graph of every ingested entity and relationship, scopable to one document or all of them.
- **Compliance dashboard** — flags overdue maintenance with severity and evidence, pulled live from the graph.
- **Graph-RAG fusion** — when a question references a known equipment tag, a direct Neo4j lookup runs alongside vector search and is injected into the LLM context as an authoritative, citable fact, so entity lookups don't depend on vector search ranking the right chunk highest.
- **Provenance-aware deletion** — removing a document trims only *its* contribution to shared graph nodes/relationships, without breaking entities other documents still reference.

## Architecture

**Ingestion** — PDF (PyMuPDF/pdfplumber), CSV, XLSX, DXF (via `ezdxf`), and standalone image documents are chunked and embedded (Cohere), with each chunk's source filename and page number carried through for citations. Pages with no extractable text (scanned pages) and standalone images go through OCR via the OCR.space cloud API — a plain outbound HTTPS call, so it runs identically in local development and in production with no local OCR binary or extra infrastructure. If the OCR call fails for any reason, that page/image is skipped with a warning rather than failing the whole upload.

**Vector RAG** — chunks are embedded into Qdrant; a question is embedded and searched the same way, with results fed to the LLM (Groq) as grounded context.

**Knowledge graph** — the same ingestion pass extracts entities (Equipment, MaintenanceEvent, Person, Substance, Regulation, Location, Document) and relationships into Neo4j, deduplicating shared entities across documents via `MERGE` on natural keys.

**Graph-RAG fusion** — direct graph lookups and vector search results are fused into a single, citation-backed context before reaching the LLM, so table/entity questions aren't at the mercy of similarity-search ranking.

**Compliance agent** — queries the graph for overdue `MaintenanceEvent` nodes and surfaces them with severity and evidence, using the same document/corpus scoping pattern as chat and the graph explorer — no separate data store.

## Tech Stack

| Layer | Tool |
|---|---|
| Backend | FastAPI (Python 3.11) |
| Frontend | Next.js (TypeScript) + Tailwind CSS |
| Embeddings | Cohere `embed-english-v3.0` |
| Vector store | Qdrant |
| Knowledge graph | Neo4j |
| LLM | Groq (multi-model fallback chain) |
| OCR | OCR.space (cloud API) |
| CAD parsing | ezdxf |
| Graph visualization | react-force-graph-2d |

## Project Structure

```
backend/    FastAPI service (Python 3.11)
frontend/   Next.js app (TypeScript, Tailwind)
```

## Getting Started

### Backend

```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Create `backend/.env` (never commit this file):

```
COHERE_API_KEY=
QDRANT_URL=
QDRANT_API_KEY=
NEO4J_URI=
NEO4J_USER=
NEO4J_PASSWORD=
GROQ_API_KEY=
OCR_SPACE_API_KEY=
```

Cohere, Qdrant, Groq, and OCR.space all have free tiers; Neo4j AuraDB Free works for a demo-sized corpus. `GET /health` should return `200 {"status": "ok"}` once the server is up.

> OCR runs entirely through the OCR.space cloud API — no local/system install required. Without a key, uploads still work; scanned or image-only pages are simply skipped with a warning instead of OCR'd.

To load the bundled sample documents (`backend/sample_docs/`) into a fresh Neo4j + Qdrant:

```bash
.venv/bin/python scripts/run_graph_pipeline.py --clear
.venv/bin/python scripts/run_ingestion_pipeline.py
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

By default the frontend talks to `http://localhost:8000`. If the backend runs elsewhere, create `frontend/.env.local`:

```
NEXT_PUBLIC_API_BASE_URL=
```

## Known Limitations

- **DWG files** (proprietary Autodesk binary format) aren't supported — there's no open-source parser for it. Export to DXF instead.

## Acknowledgments

OpsBrain was originally built as a team project for the ET AI Hackathon 2026 (Problem Statement #8: Unified Asset & Operations Brain).
