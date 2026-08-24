# OpsBrain

Industrial Knowledge Intelligence Platform — ET AI Hackathon 2026, Problem Statement #8 (Unified Asset & Operations Brain).

Upload equipment manuals, maintenance reports, safety data sheets, and inspection logs, then ask plain-English questions and get cited answers grounded in that content. A Neo4j knowledge graph sits alongside vector search so relational questions ("what regulations apply to what this equipment handles?") are answered by deterministic graph traversal instead of similarity-search guesswork, and a compliance agent flags overdue maintenance directly from the graph.

See `CLAUDE.md` for the full original product spec and build plan.

## Architecture

- **Ingestion** — PDF (PyMuPDF/pdfplumber), CSV, XLSX, DXF (CAD drawing), and standalone PNG/JPG/JPEG image documents are chunked and embedded (Cohere), with each chunk's `source_filename`/`page_number` carried through for citations. A PDF page with no extractable text (a scanned page, not a separate upload type) and a standalone image upload both go through OCR via the [OCR.space](https://ocr.space/ocrapi) cloud API — a plain outbound HTTPS call, so it works identically on this dev machine and on Render with zero infrastructure changes (no local Tesseract binary, no Docker); if the call fails for any reason (missing key, network error, rate limit, oversized file) that page/image is skipped with a warning rather than failing the upload.
- **Vector RAG** — chunks are embedded into Qdrant; a question is embedded and searched the same way, with results fed to the LLM (Groq) as grounded context.
- **Knowledge graph** — the same ingestion pass extracts entities (Equipment, MaintenanceEvent, Person, Substance, Regulation, Location, Document) and relationships into Neo4j, deduping shared entities across documents via `MERGE` on natural keys.
- **Graph-RAG fusion (F4)** — when a question references a known equipment tag, a direct Neo4j lookup runs alongside vector search and is injected into the LLM context as an authoritative fact (with its own citation), so table/entity lookups don't depend on vector search ranking the right chunk highest.
- **Compliance agent** — queries the graph for overdue `MaintenanceEvent` nodes and surfaces them with severity and evidence, no separate data store; scopable to one document or the whole corpus via `GET /compliance?document=...`, same pattern as `/graph` and `/chat`.
- **Document deletion** — provenance-aware: shared graph nodes/relationships (an entity or edge asserted by more than one document) are trimmed to remove just that document's contribution, not deleted outright, unless it was their only source.

## Tech stack

| Layer | Tool |
|---|---|
| Backend | FastAPI (Python 3.11) |
| Embeddings | Cohere `embed-english-v3.0` |
| Vector store | Qdrant |
| Knowledge graph | Neo4j |
| LLM | Groq (multi-model fallback chain) |
| OCR | OCR.space (cloud API) — scanned PDF pages and standalone PNG/JPG/JPEG |
| CAD parsing | ezdxf (DXF only) |
| Frontend | Next.js (TypeScript) + Tailwind |
| Graph visualization | react-force-graph-2d |

## Structure

- `backend/` — FastAPI service (Python 3.11)
- `frontend/` — Next.js app (TypeScript, Tailwind)

## Features

- **Document upload** — drag-and-drop PDF (including scanned pages), CSV, XLSX, DXF (CAD drawings), and standalone PNG/JPG/JPEG images, with live per-file status, toast notifications on add and on index success/failure, and delete. Newest upload appears at the top of the list. DWG (proprietary Autodesk binary format) is a deliberate limitation, not supported - there is no open-source parser for it; export to DXF instead.
- **Chat with citations** — every claim in an answer is cited back to its source document and page; scope a question to a single document or the whole corpus.
- **Knowledge graph explorer** — interactive force-directed graph of every ingested entity and relationship, scopable to one document or all of them.
- **Compliance dashboard** — flags overdue maintenance with severity and evidence, pulled live from the graph; scopable to one document or all of them, same "This document" / "All documents" toggle as chat and the graph explorer.
- **Document deletion** — remove a document's contribution to the graph and vector store without breaking entities other documents still reference.

## Running locally

### Backend

```
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Create `backend/.env` (never commit this file) with:

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

Cohere, Qdrant, Groq, and OCR.space all have free tiers; Neo4j AuraDB Free works for a demo-sized corpus. `GET /health` should return `200 {"status":"ok"}` once the server is up.

OCR runs via the [OCR.space](https://ocr.space/ocrapi) cloud API — sign up for a free key and set `OCR_SPACE_API_KEY`, no local/system install required. Without it, uploads still work; scanned/image-only PDF pages and standalone image uploads are just skipped with a warning instead of OCR'd.

To load the bundled sample documents (`backend/sample_docs/`) into a fresh Neo4j + Qdrant:

```
.venv/bin/python scripts/run_graph_pipeline.py --clear
.venv/bin/python scripts/run_ingestion_pipeline.py
```

### Frontend

```
cd frontend
npm install
npm run dev
```

By default the frontend talks to `http://localhost:8000`. If the backend runs elsewhere, create `frontend/.env.local` with:

```
NEXT_PUBLIC_API_BASE_URL=
```
