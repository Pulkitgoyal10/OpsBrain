const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/**
 * Lightweight existence check backed by GET /graph. limit=1 keeps the
 * request cheap - we only need to know whether anything has been ingested,
 * not fetch the graph itself. Any failure (backend down, network error)
 * degrades to false rather than throwing, so the landing page falls back to
 * showing the Upload button instead of crashing.
 */
export async function hasIngestedDocuments(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE_URL}/graph?limit=1`, { cache: "no-store" });
    if (!res.ok) return false;
    const data = await res.json();
    return Array.isArray(data.nodes) && data.nodes.length > 0;
  } catch {
    return false;
  }
}

const LAST_UPLOADED_DOCUMENT_KEY = "opsbrain:lastUploadedDocument";

/**
 * Tracks which document to default the graph modal's "This document" scope
 * to - the most recently successfully-indexed upload. Uses the exact
 * filename backend/app/main.py's /upload returns (== Document.filename in
 * the graph, per app/graph/pipeline.py), so it can be passed straight
 * through to GET /graph?document=... with no transformation.
 */
export function setLastUploadedDocument(filename: string): void {
  try {
    localStorage.setItem(LAST_UPLOADED_DOCUMENT_KEY, filename);
  } catch {
    // Storage can be unavailable (private browsing, disabled storage) -
    // degrades to no remembered document rather than crashing the upload.
  }
}

export function getLastUploadedDocument(): string | null {
  try {
    return localStorage.getItem(LAST_UPLOADED_DOCUMENT_KEY);
  } catch {
    return null;
  }
}
