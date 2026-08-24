"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import {
  IconChartDots3,
  IconMaximize,
  IconMinimize,
  IconShieldCheck,
  IconUpload,
  IconX,
} from "@tabler/icons-react";
import GlassIcons from "@/components/GlassIcons";
import ClickSpark from "@/components/ClickSpark";
import AnimatedList from "@/components/AnimatedList";
import { getLastUploadedDocument } from "@/lib/api";

// react-force-graph-2d's canvas rendering touches `window` at module load -
// must stay client-only, never SSR'd. Using the dedicated 2D-only package
// (default export) instead of the combined react-force-graph package,
// which bundles the VR/AFRAME variant and crashes on load without it.
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
});

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

interface CitationOut {
  source_filename: string;
  page_number: number | null;
}

interface ChatResponseBody {
  answer: string;
  citations: CitationOut[];
  graph_citations: CitationOut[];
  graph_context: unknown[];
}

type Message =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "assistant"; text: string; citations: CitationOut[] }
  | { id: string; role: "error"; text: string };

interface ComplianceItemOut {
  equipment_tag: string;
  location: string | null;
  next_due_date: string;
  days_overdue: number;
  severity: string; // "high" | "medium" | "low"
  issue_found: string;
  source_document: string;
  page_number: number | null;
}

interface ComplianceResponseBody {
  items: ComplianceItemOut[];
  summary: string;
}

// high/medium/low per the visual spec; anything unrecognized falls back to
// the low treatment rather than throwing, since severity comes verbatim
// from the backend.
function severityBorder(severity: string): string {
  if (severity === "high") return "6px solid #FF5500";
  if (severity === "medium") return "6px solid #D91656";
  return "4px solid #A68A91";
}

interface GraphNodeOut {
  id: string;
  label: string;
  properties: Record<string, unknown>;
}

interface GraphEdgeOut {
  source: string;
  target: string;
  type: string;
}

interface GraphResponseBody {
  nodes: GraphNodeOut[];
  edges: GraphEdgeOut[];
}

// Single-accent palette (CLAUDE.md's 6-node schema plus Document): shades
// and tints of crimson, plus white/gray, per label - no new hues.
const GRAPH_LABEL_COLORS: Record<string, string> = {
  Document: "#FDFCFD",
  Equipment: "#D91656",
  MaintenanceEvent: "#FF6B93",
  Regulation: "#8C0F3B",
  Person: "#C9A8AF",
  Substance: "#6B4A52",
  Location: "#A68A91",
};
const DEFAULT_NODE_COLOR = "#A68A91";

function graphNodeColor(label: string): string {
  return GRAPH_LABEL_COLORS[label] ?? DEFAULT_NODE_COLOR;
}

// Mirrors backend app/graph/schema.py's NATURAL_KEY - which property holds
// each label's human-readable identity, for the click panel's title.
const NATURAL_KEY_PROPERTY: Record<string, string> = {
  Document: "filename",
  Equipment: "tag",
  MaintenanceEvent: "event_id",
  Person: "name",
  Substance: "name",
  Regulation: "code",
  Location: "name",
};

function graphNodeTitle(node: GraphNodeOut): string {
  const key = NATURAL_KEY_PROPERTY[node.label];
  const value = key ? node.properties[key] : undefined;
  return typeof value === "string" && value ? value : node.id;
}

// source_chunk_ids/sources are internal provenance arrays of chunk-id
// strings - real data, but not "key properties" for a glance-panel.
const HIDDEN_PROPERTY_KEYS = new Set(["source_chunk_ids", "sources"]);

function graphNodeDetailEntries(node: GraphNodeOut): [string, string][] {
  return Object.entries(node.properties)
    .filter(([k]) => !HIDDEN_PROPERTY_KEYS.has(k))
    .map(([k, v]) => [k, Array.isArray(v) ? v.join(", ") : String(v)]);
}

// Chosen against the real corpus (196 nodes/291 edges): degree >= 4 labels
// ~24 nodes across every type incl. several Equipment tags and 4/5
// Documents, without flooding the view - degree alone skews toward
// Document/Location/Substance hubs (up to 97), so a low absolute cutoff
// rather than a top-N-per-type rule keeps Equipment visible too.
const HUB_DEGREE_THRESHOLD = 4;

function computeNodeDegrees(links: { source: string; target: string }[]): Map<string, number> {
  const degrees = new Map<string, number>();
  for (const link of links) {
    degrees.set(link.source, (degrees.get(link.source) ?? 0) + 1);
    degrees.set(link.target, (degrees.get(link.target) ?? 0) + 1);
  }
  return degrees;
}

function CitationChip({ citation }: { citation: CitationOut }) {
  const label =
    citation.page_number != null
      ? `${citation.source_filename}, page ${citation.page_number}`
      : citation.source_filename;
  return (
    <span className="mt-4 mr-2 inline-block bg-carbon text-stark text-[0.85rem] px-3 py-[0.35rem] rounded border border-crimson/30">
      {label}
    </span>
  );
}

interface DocumentOut {
  filename: string;
  doc_type: string;
  title: string;
  ingested_at: string;
}

function ScopeToggle({
  label,
  scope,
  onChange,
  filename,
  showFilename = true,
}: {
  label: string;
  scope: "document" | "all";
  onChange: (scope: "document" | "all") => void;
  filename: string | null;
  showFilename?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 sm:gap-3">
      <span className="text-ash-rose text-sm">{label}</span>
      <div className="inline-flex items-center bg-carbon border border-maroon rounded-full p-1">
        <button
          onClick={() => onChange("document")}
          className={`text-sm px-3 sm:px-4 py-1.5 rounded-full transition-colors ${
            scope === "document"
              ? "bg-crimson text-stark font-medium"
              : "text-ash-rose hover:text-stark"
          }`}
        >
          This document
        </button>
        <button
          onClick={() => onChange("all")}
          className={`text-sm px-3 sm:px-4 py-1.5 rounded-full transition-colors ${
            scope === "all" ? "bg-crimson text-stark font-medium" : "text-ash-rose hover:text-stark"
          }`}
        >
          All documents
        </button>
      </div>
      {showFilename && scope === "document" && filename && (
        <span className="text-ash-rose text-xs truncate">{filename}</span>
      )}
    </div>
  );
}

// Shared by the chat scope bar and the graph modal's scope bar - same
// GET /documents-backed list, same "pick one" affordance, so the two never
// drift into different dropdown behavior.
function DocumentPicker({
  documents,
  value,
  onChange,
}: {
  documents: DocumentOut[];
  value: string | null;
  onChange: (filename: string) => void;
}) {
  return (
    <select
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      className="bg-carbon text-stark text-sm border border-maroon rounded-md px-3 py-1.5 w-full sm:w-auto sm:max-w-[280px]"
    >
      {documents.map((d) => (
        <option key={d.filename} value={d.filename}>
          {d.filename}
        </option>
      ))}
    </select>
  );
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [graphOpen, setGraphOpen] = useState(false);
  const [complianceOpen, setComplianceOpen] = useState(false);
  const [complianceItems, setComplianceItems] = useState<ComplianceItemOut[] | null>(null);
  const [complianceSummary, setComplianceSummary] = useState("");
  const [complianceLoading, setComplianceLoading] = useState(false);
  const [complianceError, setComplianceError] = useState<string | null>(null);
  const [complianceScope, setComplianceScope] = useState<"document" | "all">("all");
  const [complianceScopedFilename, setComplianceScopedFilename] = useState<string | null>(null);
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>([]);
  const [chatScope, setChatScope] = useState<"document" | "all">("all");
  const [chatScopedFilename, setChatScopedFilename] = useState<string | null>(null);
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    // Same default logic as the empty-state chips: default to "This
    // document" when something was just uploaded (there's a concrete
    // document to scope to), "All documents" otherwise. Reads the real
    // document list (not just local upload-session state) so the picker
    // below can offer every ingested document, including ones from a
    // previous session - and so a stale lastDoc pointing at a since-
    // deleted document falls back to the first available one instead of
    // silently scoping to nothing.
    const lastDoc = getLastUploadedDocument();

    fetch(`${API_BASE_URL}/documents`)
      .then((res) => (res.ok ? res.json() : { documents: [] }))
      .then((body: { documents?: DocumentOut[] }) => {
        if (cancelled) return;
        const docs = body.documents ?? [];
        setDocuments(docs);
        const defaultFilename =
          lastDoc && docs.some((d) => d.filename === lastDoc) ? lastDoc : (docs[0]?.filename ?? null);
        setChatScopedFilename(defaultFilename);
        setChatScope(defaultFilename ? "document" : "all");
      })
      .catch(() => {
        if (cancelled) return;
        setDocuments([]);
        setChatScopedFilename(null);
        setChatScope("all");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    fetch(`${API_BASE_URL}/suggested-questions`)
      .then((res) => (res.ok ? res.json() : { questions: [] }))
      .then((body: { questions?: string[] }) => {
        if (!cancelled) setSuggestedQuestions(body.questions ?? []);
      })
      // Empty database, a fresh corpus, or a backend hiccup all collapse to
      // the same "no chips" fallback (empty state's <p> renders alone) -
      // no need to distinguish the reason in the UI.
      .catch(() => {
        if (!cancelled) setSuggestedQuestions([]);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!complianceOpen) return;
    let cancelled = false;

    setComplianceLoading(true);
    setComplianceError(null);

    const url =
      complianceScope === "document" && complianceScopedFilename
        ? `${API_BASE_URL}/compliance?document=${encodeURIComponent(complianceScopedFilename)}`
        : `${API_BASE_URL}/compliance`;

    fetch(url)
      .then(async (res) => {
        const data = await res.json().catch(() => null);
        if (!res.ok) {
          const detail =
            data && typeof data === "object" && "detail" in data
              ? String((data as { detail: unknown }).detail)
              : "Failed to load compliance data";
          throw new Error(detail);
        }
        return data as ComplianceResponseBody;
      })
      .then((body) => {
        if (cancelled) return;
        setComplianceItems(body.items);
        setComplianceSummary(body.summary);
      })
      .catch((err) => {
        if (cancelled) return;
        setComplianceError(err instanceof Error ? err.message : "Network error");
      })
      .finally(() => {
        if (!cancelled) setComplianceLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [complianceOpen, complianceScope, complianceScopedFilename]);

  const openCompliance = useCallback(() => {
    // Seeded synchronously, same reasoning as openGraph: the fetch effect
    // above must see the right scope/filename on its first run, not fire
    // once with stale defaults and again once state catches up.
    const lastDoc = getLastUploadedDocument();
    const defaultFilename =
      lastDoc && documents.some((d) => d.filename === lastDoc)
        ? lastDoc
        : (documents[0]?.filename ?? null);
    setComplianceScopedFilename(defaultFilename);
    setComplianceScope(defaultFilename ? "document" : "all");
    setComplianceOpen(true);
  }, [documents]);

  const closeCompliance = useCallback(() => {
    setComplianceOpen(false);
    inputRef.current?.focus();
  }, []);

  const [graphNodes, setGraphNodes] = useState<GraphNodeOut[]>([]);
  const [graphLinks, setGraphLinks] = useState<{ source: string; target: string; type: string }[]>(
    []
  );
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNodeOut | null>(null);
  const [graphMaximized, setGraphMaximized] = useState(false);
  // Collapsed by default on mobile only (sm:flex below always shows it on
  // larger screens regardless of this) - a full 7-row color key is a lot of
  // permanent screen-space to spend over the canvas on a narrow phone.
  const [legendOpen, setLegendOpen] = useState(false);
  const [graphScope, setGraphScope] = useState<"document" | "all">("all");
  const [scopedFilename, setScopedFilename] = useState<string | null>(null);
  const graphContainerRef = useRef<HTMLDivElement>(null);
  const [graphDims, setGraphDims] = useState({ width: 700, height: 500 });
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fgRef = useRef<any>(null);

  const graphNodeDegrees = useMemo(() => computeNodeDegrees(graphLinks), [graphLinks]);

  useEffect(() => {
    if (!graphOpen) return;
    let cancelled = false;

    setGraphLoading(true);
    setGraphError(null);
    setSelectedNode(null);
    setGraphMaximized(false);

    const url =
      graphScope === "document" && scopedFilename
        ? `${API_BASE_URL}/graph?document=${encodeURIComponent(scopedFilename)}`
        : `${API_BASE_URL}/graph`;

    fetch(url)
      .then(async (res) => {
        const data = await res.json().catch(() => null);
        if (!res.ok) {
          const detail =
            data && typeof data === "object" && "detail" in data
              ? String((data as { detail: unknown }).detail)
              : "Failed to load graph data";
          throw new Error(detail);
        }
        return data as GraphResponseBody;
      })
      .then((body) => {
        if (cancelled) return;
        setGraphNodes(body.nodes);
        setGraphLinks(body.edges.map((e) => ({ source: e.source, target: e.target, type: e.type })));
      })
      .catch((err) => {
        if (cancelled) return;
        setGraphError(err instanceof Error ? err.message : "Network error");
      })
      .finally(() => {
        if (!cancelled) setGraphLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [graphOpen, graphScope, scopedFilename]);

  useEffect(() => {
    if (!graphOpen) return;
    const el = graphContainerRef.current;
    if (!el) return;

    const update = () => setGraphDims({ width: el.clientWidth, height: el.clientHeight });
    update();

    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [graphOpen]);

  const openGraph = useCallback(() => {
    // Seeded synchronously (not in a useEffect keyed on graphOpen) so the
    // fetch effect sees the right scope/filename on its very first run
    // instead of firing once with stale defaults and again once state
    // catches up - one GET /graph per open, not two.
    //
    // Same default logic as the chat scope bar: prefer the last-uploaded
    // document if it's still a real, currently-ingested document, else fall
    // back to the first one in the list rather than a stale/deleted
    // filename. `documents` is already populated by the mount effect above
    // by the time a user can click the Graph button.
    const lastDoc = getLastUploadedDocument();
    const defaultFilename =
      lastDoc && documents.some((d) => d.filename === lastDoc)
        ? lastDoc
        : (documents[0]?.filename ?? null);
    setScopedFilename(defaultFilename);
    setGraphScope(defaultFilename ? "document" : "all");
    setGraphOpen(true);
  }, [documents]);

  const closeGraph = useCallback(() => {
    setGraphOpen(false);
    inputRef.current?.focus();
  }, []);

  const sendQuestion = useCallback(async (question: string) => {
    const trimmed = question.trim();
    if (!trimmed) return;

    setInput("");
    setMessages((prev) => [...prev, { id: `u-${Date.now()}`, role: "user", text: trimmed }]);
    setPending(true);

    try {
      const res = await fetch(`${API_BASE_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: trimmed,
          ...(chatScope === "document" && chatScopedFilename
            ? { document: chatScopedFilename }
            : {}),
        }),
      });
      const data = await res.json().catch(() => null);

      if (!res.ok) {
        const detail =
          data && typeof data === "object" && "detail" in data
            ? String((data as { detail: unknown }).detail)
            : "Request failed";
        setMessages((prev) => [...prev, { id: `e-${Date.now()}`, role: "error", text: detail }]);
        return;
      }

      const body = data as ChatResponseBody;
      setMessages((prev) => [
        ...prev,
        {
          id: `a-${Date.now()}`,
          role: "assistant",
          text: body.answer,
          citations: body.citations,
        },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: `e-${Date.now()}`,
          role: "error",
          text: err instanceof Error ? err.message : "Network error",
        },
      ]);
    } finally {
      setPending(false);
    }
  }, [chatScope, chatScopedFilename]);

  const handleSend = useCallback(() => {
    if (pending) return;
    sendQuestion(input);
  }, [input, pending, sendQuestion]);

  const topBarIcons = [
    {
      icon: <IconChartDots3 size={28} color="#FFFFFF" />,
      color: "#D91656",
      label: "Graph",
      onClick: openGraph,
    },
    {
      icon: <IconShieldCheck size={28} color="#FFFFFF" />,
      color: "#D91656",
      label: "Compliance",
      onClick: openCompliance,
    },
    {
      icon: <IconUpload size={28} color="#FFFFFF" />,
      color: "#D91656",
      label: "Upload",
      onClick: () => router.push("/upload"),
    },
  ];

  return (
    <div className="h-screen supports-[height:100dvh]:h-dvh flex flex-col overflow-hidden">
      <header
        className="flex items-center justify-between px-4 sm:px-6 md:px-8"
        style={{ height: "70px" }}
      >
        <Link
          href="/"
          className="flex items-center text-lg sm:text-xl md:text-2xl font-black shrink-0"
          style={{ textShadow: "0 2px 8px rgba(0, 0, 0, 0.8)", gap: "0.625rem" }}
        >
          <img src="/logo-icon.svg" alt="" className="h-6 sm:h-7 md:h-8 w-auto" />
          <span>
            <span className="text-stark">Ops</span>
            <span className="text-crimson">Brain</span>
          </span>
        </Link>
        <GlassIcons items={topBarIcons} className="topbar-glass-icons" />
      </header>

      <main className="flex-grow overflow-y-auto flex flex-col items-center px-4 sm:px-6 md:px-8 py-8 sm:py-12">
        <div className="max-w-4xl w-full flex flex-col gap-8">
          {messages.length === 0 ? (
            <div className="flex flex-col items-center text-center gap-6 mt-8 sm:mt-16">
              <p className="text-lg sm:text-xl text-ash-rose">
                Ask anything about your uploaded documents
              </p>
              {suggestedQuestions.length > 0 && (
                <div className="flex flex-wrap justify-center gap-2 sm:gap-3">
                  {suggestedQuestions.map((q) => (
                    <button
                      key={q}
                      onClick={() => {
                        setInput(q);
                        inputRef.current?.focus();
                      }}
                      className="bg-carbon text-stark text-sm px-4 py-2.5 rounded-full border border-crimson/30 hover:border-crimson transition-colors"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            messages.map((m) => {
              if (m.role === "user") {
                return (
                  <div
                    key={m.id}
                    className="self-end bg-crimson text-stark max-w-[90%] sm:max-w-[75%]"
                    style={{ padding: "1.25rem 1.5rem", borderRadius: "1rem 1rem 0 1rem" }}
                  >
                    {m.text}
                  </div>
                );
              }
              if (m.role === "assistant") {
                return (
                  <div
                    key={m.id}
                    className="self-start bg-maroon text-stark max-w-[90%] sm:max-w-[75%]"
                    style={{ padding: "1.25rem 1.5rem", borderRadius: "1rem 1rem 1rem 0" }}
                  >
                    <div>{m.text}</div>
                    {m.citations.length > 0 && (
                      <div>
                        {m.citations.map((c, i) => (
                          <CitationChip key={i} citation={c} />
                        ))}
                      </div>
                    )}
                  </div>
                );
              }
              return (
                <div
                  key={m.id}
                  className="self-start bg-maroon text-status max-w-[90%] sm:max-w-[75%]"
                  style={{ padding: "1.25rem 1.5rem", borderRadius: "1rem 1rem 1rem 0" }}
                >
                  {m.text}
                </div>
              );
            })
          )}

          {pending && (
            <div
              className="self-start bg-maroon text-ash-rose max-w-[90%] sm:max-w-[75%]"
              style={{ padding: "1.25rem 1.5rem", borderRadius: "1rem 1rem 1rem 0" }}
            >
              Thinking...
            </div>
          )}
        </div>
      </main>

      {documents.length > 0 && (
        <div className="flex justify-center px-4 sm:px-8">
          <div className="max-w-4xl w-full pb-3 flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3">
            <ScopeToggle
              label="Answer from:"
              scope={chatScope}
              onChange={setChatScope}
              filename={chatScopedFilename}
              showFilename={false}
            />
            {chatScope === "document" && (
              <DocumentPicker
                documents={documents}
                value={chatScopedFilename}
                onChange={setChatScopedFilename}
              />
            )}
          </div>
        </div>
      )}

      <div className="flex justify-center px-4 sm:px-8 py-4 sm:py-8">
        <div
          className="max-w-4xl w-full flex items-center border border-maroon"
          style={{
            borderRadius: "0.75rem",
            padding: "0.75rem",
            backgroundColor: "rgba(18, 8, 10, 0.75)",
            backdropFilter: "blur(12px)",
          }}
        >
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSend();
            }}
            placeholder="Ask a question..."
            className="flex-grow bg-transparent border-none text-stark outline-none placeholder:text-ash-rose"
            style={{ padding: "0.5rem 1rem" }}
          />
          <div className="w-fit shrink-0">
            <ClickSpark sparkColor="#FDFCFD">
              <button
                onClick={handleSend}
                disabled={pending || !input.trim()}
                className="bg-crimson text-stark disabled:opacity-40"
                style={{ padding: "0.75rem 1.5rem", borderRadius: "0.5rem" }}
              >
                Send
              </button>
            </ClickSpark>
          </div>
        </div>
      </div>

      {graphOpen && (
        <div
          className="absolute inset-0 z-50 flex items-center justify-center bg-void/90 backdrop-blur"
          onClick={closeGraph}
        >
          <div
            className={`flex flex-col bg-carbon border border-maroon rounded-2xl w-[95vw] h-[88vh] ${
              graphMaximized ? "sm:w-[90vw] sm:h-[90vh]" : "sm:w-[700px] sm:h-[500px]"
            }`}
            onClick={(e) => e.stopPropagation()}
          >
            <div
              className="flex justify-between items-center px-4 sm:px-8 py-4 sm:py-8"
              style={{ borderBottom: "1px solid #240F14" }}
            >
              <h2 className="text-stark font-bold text-xl sm:text-2xl">Knowledge graph</h2>
              <div className="flex items-center gap-4">
                <button
                  onClick={() => setGraphMaximized((v) => !v)}
                  aria-label={graphMaximized ? "Minimize" : "Maximize"}
                  className="hidden sm:block text-ash-rose hover:text-stark transition-colors"
                >
                  {graphMaximized ? <IconMinimize size={22} /> : <IconMaximize size={22} />}
                </button>
                <button
                  onClick={closeGraph}
                  aria-label="Close knowledge graph"
                  className="text-ash-rose hover:text-stark transition-colors p-2 -m-2"
                >
                  <IconX size={24} />
                </button>
              </div>
            </div>

            {documents.length > 0 && (
              <div
                className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 px-4 sm:px-8 py-3"
                style={{ borderBottom: "1px solid #240F14" }}
              >
                <ScopeToggle
                  label="Scope:"
                  scope={graphScope}
                  onChange={setGraphScope}
                  filename={scopedFilename}
                  showFilename={false}
                />
                {graphScope === "document" && (
                  <DocumentPicker
                    documents={documents}
                    value={scopedFilename}
                    onChange={setScopedFilename}
                  />
                )}
              </div>
            )}

            <div
              ref={graphContainerRef}
              className="relative flex-grow"
              style={{ backgroundColor: "#050204" }}
            >
              {graphLoading && (
                <p className="absolute inset-0 flex items-center justify-center text-ash-rose">
                  Loading graph...
                </p>
              )}

              {graphError && (
                <p className="absolute inset-0 flex items-center justify-center text-status text-center px-6">
                  {graphError}
                </p>
              )}

              {!graphLoading && !graphError && graphNodes.length === 0 && (
                <p className="absolute inset-0 flex items-center justify-center text-ash-rose text-center px-6">
                  No graph data yet.
                </p>
              )}

              {!graphLoading && !graphError && graphNodes.length > 0 && (
                <>
                  <ForceGraph2D
                    ref={fgRef}
                    graphData={{ nodes: graphNodes, links: graphLinks }}
                    width={graphDims.width}
                    height={graphDims.height}
                    backgroundColor="#050204"
                    nodeId="id"
                    nodeVal={(n) => 1 + (graphNodeDegrees.get((n as GraphNodeOut).id) ?? 0)}
                    nodeLabel={(n) =>
                      `${(n as GraphNodeOut).label}: ${graphNodeTitle(n as GraphNodeOut)}`
                    }
                    nodeCanvasObject={(n, ctx, globalScale) => {
                      const node = n as GraphNodeOut & { x: number; y: number };
                      const degree = graphNodeDegrees.get(node.id) ?? 0;
                      const radius = 2.5 + Math.sqrt(degree) * 1.8;

                      ctx.beginPath();
                      ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI);
                      ctx.fillStyle = graphNodeColor(node.label);
                      ctx.fill();

                      // Always-on labels for hub nodes (Equipment/Document
                      // and other high-degree nodes) so the graph reads
                      // without requiring a click - see HUB_DEGREE_THRESHOLD.
                      if (degree >= HUB_DEGREE_THRESHOLD) {
                        const fontSize = Math.max(11 / globalScale, 3.5);
                        ctx.font = `${fontSize}px sans-serif`;
                        ctx.textAlign = "left";
                        ctx.textBaseline = "middle";
                        ctx.fillStyle = "#FDFCFD";
                        ctx.fillText(graphNodeTitle(node), node.x + radius + 2, node.y);
                      }
                    }}
                    nodePointerAreaPaint={(n, color, ctx) => {
                      const node = n as GraphNodeOut & { x: number; y: number };
                      const degree = graphNodeDegrees.get(node.id) ?? 0;
                      const radius = 2.5 + Math.sqrt(degree) * 1.8;
                      ctx.beginPath();
                      ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI);
                      ctx.fillStyle = color;
                      ctx.fill();
                    }}
                    linkColor={() => "rgba(166, 138, 145, 0.25)"}
                    linkWidth={1}
                    onNodeClick={(n) => setSelectedNode(n as GraphNodeOut)}
                    onEngineStop={() =>
                      fgRef.current?.zoomToFit(
                        600,
                        60,
                        // Exclude degree-0 orphan nodes (real extraction
                        // noise in this corpus, e.g. stray Location nodes
                        // like "Delhi"/"Test cases") from the fit bbox -
                        // d3-force scatters disconnected nodes far from the
                        // main graph, which would otherwise force a much
                        // wider zoom-out than the actual connected clusters
                        // need.
                        (node: GraphNodeOut) => (graphNodeDegrees.get(node.id) ?? 0) > 0
                      )
                    }
                  />

                  {!legendOpen && (
                    <button
                      onClick={() => setLegendOpen(true)}
                      aria-label="Show legend"
                      className="sm:hidden absolute bottom-3 left-3 rounded-lg text-xs text-ash-rose"
                      style={{
                        backgroundColor: "rgba(18, 8, 10, 0.85)",
                        padding: "0.6rem 0.85rem",
                        border: "1px solid #240F14",
                      }}
                    >
                      Legend
                    </button>
                  )}

                  <div
                    className={`absolute bottom-3 left-3 flex-col gap-1 rounded-lg ${
                      legendOpen ? "flex" : "hidden"
                    } sm:flex`}
                    style={{
                      backgroundColor: "rgba(18, 8, 10, 0.85)",
                      padding: "0.75rem",
                      border: "1px solid #240F14",
                    }}
                  >
                    <button
                      onClick={() => setLegendOpen(false)}
                      aria-label="Hide legend"
                      className="sm:hidden self-end text-ash-rose hover:text-stark -mt-1 -mr-1 p-1"
                    >
                      <IconX size={12} />
                    </button>
                    {Object.entries(GRAPH_LABEL_COLORS).map(([label, color]) => (
                      <div key={label} className="flex items-center gap-2 text-xs text-ash-rose">
                        <span
                          style={{
                            width: 8,
                            height: 8,
                            borderRadius: "50%",
                            backgroundColor: color,
                            display: "inline-block",
                          }}
                        />
                        {label}
                      </div>
                    ))}
                  </div>

                  {selectedNode && (
                    <div
                      className="absolute top-3 right-3 left-3 sm:left-auto max-w-none sm:max-w-[260px] rounded-lg"
                      style={{
                        backgroundColor: "rgba(18, 8, 10, 0.95)",
                        padding: "1rem",
                        border: "1px solid #240F14",
                      }}
                    >
                      <div className="flex justify-between items-start gap-2 mb-2">
                        <div>
                          <div className="text-stark font-bold text-sm">
                            {graphNodeTitle(selectedNode)}
                          </div>
                          <div className="text-ash-rose text-xs">{selectedNode.label}</div>
                        </div>
                        <button
                          onClick={() => setSelectedNode(null)}
                          aria-label="Dismiss node details"
                          className="text-ash-rose hover:text-stark"
                        >
                          <IconX size={16} />
                        </button>
                      </div>
                      <div className="flex flex-col gap-1">
                        {graphNodeDetailEntries(selectedNode).map(([k, v]) => (
                          <div key={k} className="text-xs text-ash-rose">
                            <span className="text-stark">{k}:</span> {v}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {complianceOpen && (
        <div
          className="absolute inset-0 z-50 flex items-center justify-center bg-void/90 backdrop-blur px-4 sm:px-0"
          onClick={closeCompliance}
        >
          <div
            className="w-full max-w-[650px] max-h-[85vh] overflow-y-auto flex flex-col gap-5 sm:gap-8 bg-carbon border border-maroon rounded-2xl p-5 sm:p-10"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex justify-between items-center">
              <h2 className="text-stark font-bold text-xl sm:text-[1.75rem]">
                Compliance overview
              </h2>
              <button
                onClick={closeCompliance}
                aria-label="Close compliance overview"
                className="text-ash-rose hover:text-stark transition-colors p-2 -m-2"
              >
                <IconX size={24} />
              </button>
            </div>

            {documents.length > 0 && (
              <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3">
                <ScopeToggle
                  label="Scope:"
                  scope={complianceScope}
                  onChange={setComplianceScope}
                  filename={complianceScopedFilename}
                  showFilename={false}
                />
                {complianceScope === "document" && (
                  <DocumentPicker
                    documents={documents}
                    value={complianceScopedFilename}
                    onChange={setComplianceScopedFilename}
                  />
                )}
              </div>
            )}

            {complianceLoading && <p className="text-ash-rose">Loading compliance data...</p>}

            {complianceError && <p className="text-status">{complianceError}</p>}

            {!complianceLoading && !complianceError && complianceItems && (
              <>
                <p className="text-base sm:text-lg text-ash-rose">{complianceSummary}</p>

                {complianceItems.length === 0 ? (
                  <p className="text-center text-ash-rose">No compliance issues detected.</p>
                ) : (
                  <AnimatedList
                    items={complianceItems.map((item, i) => (
                      <div
                        key={`${item.equipment_tag}-${i}`}
                        className="bg-maroon p-4 sm:p-6 rounded-xl"
                        style={{ borderLeft: severityBorder(item.severity) }}
                      >
                        <div className="text-lg sm:text-xl text-stark font-bold">
                          {item.location
                            ? `${item.equipment_tag} — ${item.location}`
                            : item.equipment_tag}
                        </div>
                        <div className="text-sm sm:text-base text-ash-rose mt-2">
                          {item.issue_found} — Overdue by {item.days_overdue}{" "}
                          day{item.days_overdue === 1 ? "" : "s"}
                        </div>
                        <div className="text-sm text-ash-rose mt-2">
                          {item.page_number != null
                            ? `[${item.source_document}, page ${item.page_number}]`
                            : `[${item.source_document}]`}
                        </div>
                      </div>
                    ))}
                    showGradients={false}
                    enableArrowNavigation={false}
                    displayScrollbar={true}
                    className="!w-full compliance-list"
                  />
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
