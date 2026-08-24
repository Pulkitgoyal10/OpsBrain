"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { IconTrash, IconUpload } from "@tabler/icons-react";
import AnimatedList from "@/components/AnimatedList";
import { ToastStack, useToasts } from "@/components/Toast";
import { setLastUploadedDocument } from "@/lib/api";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
// Mirrors the backend's SUPPORTED_SUFFIXES (app/ingestion/loader_registry.py) -
// this is only a file-picker hint; the real 415 rejection (and its exact
// message) always comes from the backend, not a client-side guess.
const ACCEPTED_EXTENSIONS = ".pdf,.csv,.xlsx,.dxf,.png,.jpg,.jpeg";

type UploadStatus = "uploading" | "indexed" | "failed";

interface UploadResponseBody {
  status: string;
  filename: string;
  doc_type: string;
  chunks_created: number;
  graph_nodes_created: number;
  graph_relationships_created: number;
  graph_node_counts: Record<string, number>;
  warnings: string[];
}

interface UploadEntry {
  id: string;
  filename: string;
  status: UploadStatus;
  errorMessage?: string;
  response?: UploadResponseBody;
}

interface DocumentOut {
  filename: string;
  doc_type: string;
  title: string;
  ingested_at: string;
}

function StatusLabel({ status, errorMessage }: { status: UploadStatus; errorMessage?: string }) {
  if (status === "uploading") {
    return <span className="text-ash-rose">Uploading...</span>;
  }
  if (status === "indexed") {
    return <span className="text-stark">Indexed</span>;
  }
  return <span className="text-status">{errorMessage ? `Failed: ${errorMessage}` : "Failed"}</span>;
}

export default function UploadPage() {
  const [files, setFiles] = useState<UploadEntry[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const { toasts, push: pushToast, dismiss: dismissToast } = useToasts();

  // Populates the list with documents already ingested from *previous*
  // sessions (GET /documents), not just files uploaded since this page
  // load - otherwise a document uploaded before the current page load has
  // no row at all, and its delete button is unreachable.
  useEffect(() => {
    let cancelled = false;

    fetch(`${API_BASE_URL}/documents`)
      .then((res) => (res.ok ? res.json() : { documents: [] }))
      .then((body: { documents?: DocumentOut[] }) => {
        if (cancelled) return;
        const existing: UploadEntry[] = (body.documents ?? []).map((d) => ({
          id: d.filename,
          filename: d.filename,
          status: "indexed",
        }));
        setFiles((prev) => [...existing, ...prev]);
      })
      .catch(() => {
        // Backend hiccup on this one-time populate just means previously-
        // uploaded documents won't show until reload - not worth surfacing
        // as an error state on a page whose primary job is uploading.
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const uploadFile = useCallback(async (id: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);

    let data: unknown;
    let ok: boolean;
    try {
      const res = await fetch(`${API_BASE_URL}/upload`, { method: "POST", body: formData });
      ok = res.ok;
      data = await res.json().catch(() => null);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Network error";
      setFiles((prev) =>
        prev.map((f) => (f.id === id ? { ...f, status: "failed", errorMessage } : f))
      );
      pushToast(`${file.name} failed: ${errorMessage}`, "error");
      return;
    }

    if (!ok) {
      const detail =
        data && typeof data === "object" && "detail" in data
          ? String((data as { detail: unknown }).detail)
          : "Upload failed";
      setFiles((prev) =>
        prev.map((f) => (f.id === id ? { ...f, status: "failed", errorMessage: detail } : f))
      );
      pushToast(`${file.name} failed: ${detail}`, "error");
      return;
    }

    // "success" and "partial" (chunks embedded, graph write failed) both
    // mean the file is ingested and searchable - this spec only defines 3
    // status labels, so both map to Indexed rather than inventing a 4th.
    const body = data as UploadResponseBody;
    setFiles((prev) =>
      prev.map((f) => (f.id === id ? { ...f, status: "indexed", response: body } : f))
    );
    setLastUploadedDocument(body.filename);
    pushToast(`${body.filename} indexed successfully`, "success");
  }, [pushToast]);

  const deleteFile = useCallback(async (entry: UploadEntry) => {
    if (!window.confirm(`Delete "${entry.filename}"? This removes it from the knowledge base.`)) {
      return;
    }

    try {
      const res = await fetch(`${API_BASE_URL}/documents/${encodeURIComponent(entry.filename)}`, {
        method: "DELETE",
      });
      const data = await res.json().catch(() => null);

      if (!res.ok) {
        const detail =
          data && typeof data === "object" && "detail" in data
            ? String((data as { detail: unknown }).detail)
            : "Delete failed";
        setFiles((prev) =>
          prev.map((f) => (f.id === entry.id ? { ...f, status: "failed", errorMessage: detail } : f))
        );
        return;
      }

      setFiles((prev) => prev.filter((f) => f.id !== entry.id));
    } catch (err) {
      setFiles((prev) =>
        prev.map((f) =>
          f.id === entry.id
            ? {
                ...f,
                status: "failed",
                errorMessage: err instanceof Error ? err.message : "Network error",
              }
            : f
        )
      );
    }
  }, []);

  const handleFiles = useCallback(
    (fileList: FileList | null) => {
      if (!fileList) return;
      const selected = Array.from(fileList).map((file) => ({
        id: `${file.name}-${Date.now()}-${Math.random()}`,
        file,
      }));

      // Rows for every selected file appear immediately, so the full queue
      // is visible up front - but the actual uploads below run one at a
      // time, not concurrently. Newest selection goes at the top of the
      // list (most-recent-first), not appended to the bottom.
      setFiles((prev) => [
        ...selected.map(({ id, file }) => ({
          id,
          filename: file.name,
          status: "uploading" as const,
        })),
        ...prev,
      ]);

      for (const { file } of selected) {
        pushToast(`${file.name} added`);
      }

      (async () => {
        // Sequential, not parallel: POST /upload does synchronous,
        // rate-limited Cohere/Groq calls per file - firing several large
        // uploads at once risks compounding 429 retries across concurrent
        // requests instead of working through one clean queue.
        for (const { id, file } of selected) {
          await uploadFile(id, file);
        }
      })();
    },
    [uploadFile, pushToast]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      handleFiles(e.dataTransfer.files);
    },
    [handleFiles]
  );

  const hasIndexed = files.some((f) => f.status === "indexed");

  return (
    <main className="min-h-screen px-4 sm:px-8 py-10 sm:py-16">
      <div className="max-w-4xl mx-auto">
        <h1 className="text-3xl sm:text-4xl md:text-[2.5rem] font-bold text-stark mb-3">
          Upload documents
        </h1>
        <p className="text-base sm:text-lg text-ash-rose mb-8 sm:mb-12">
          Drag and drop files, or click to browse. Supported: PDF (including scanned documents),
          CSV, Excel, DXF drawings, and scanned images (PNG/JPG).
        </p>

        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
          className="w-full min-h-[220px] sm:min-h-[280px] border-2 border-dashed border-crimson rounded-2xl bg-carbon/70 flex flex-col items-center justify-center gap-4 sm:gap-5 px-4 text-center"
        >
          <IconUpload size={56} color="#D91656" stroke={1.75} />
          <p className="text-lg sm:text-xl text-stark">Drag and drop files here</p>
          <button
            onClick={() => inputRef.current?.click()}
            className="bg-maroon text-crimson px-8 py-3 rounded-lg"
          >
            Choose files
          </button>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPTED_EXTENSIONS}
            className="hidden"
            onChange={(e) => {
              handleFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </div>
        <p className="text-ash-rose text-sm mt-3">
          You can upload multiple files, but for best results and faster processing, upload one at
          a time.
        </p>

        {files.length > 0 && (
          <div className="mt-8">
            <AnimatedList
              items={files.map((f) => (
                <div
                  key={f.id}
                  className="flex flex-col sm:flex-row sm:justify-between items-start sm:items-center gap-2 sm:gap-0 p-4 sm:p-5 bg-carbon rounded-lg border-l-4 border-crimson"
                >
                  <span className="text-stark text-sm sm:text-base break-words">{f.filename}</span>
                  <div className="flex items-center justify-between sm:justify-normal gap-4 w-full sm:w-auto">
                    <StatusLabel status={f.status} errorMessage={f.errorMessage} />
                    {f.status === "indexed" && (
                      <button
                        onClick={() => deleteFile(f)}
                        aria-label={`Delete ${f.filename}`}
                        className="text-ash-rose hover:text-status transition-colors p-3 -m-3"
                      >
                        <IconTrash size={18} />
                      </button>
                    )}
                  </div>
                </div>
              ))}
              showGradients={false}
              enableArrowNavigation={false}
              displayScrollbar={false}
              className="!w-full"
            />
          </div>
        )}

        <div className="flex flex-col-reverse sm:flex-row sm:justify-between items-stretch sm:items-center gap-4 sm:gap-0 mt-8 sm:mt-12">
          <Link
            href="/"
            className="text-ash-rose text-sm hover:text-crimson transition-colors text-center sm:text-left py-2 sm:py-0"
          >
            Back to home
          </Link>
          {hasIndexed ? (
            <Link
              href="/chat"
              className="bg-crimson text-stark px-10 py-4 rounded-lg font-bold w-full sm:w-auto text-center"
            >
              Go to chat
            </Link>
          ) : (
            <button
              disabled
              className="bg-crimson text-stark px-10 py-4 rounded-lg font-bold opacity-40 cursor-not-allowed w-full sm:w-auto text-center"
            >
              Go to chat
            </button>
          )}
        </div>
      </div>
      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </main>
  );
}
