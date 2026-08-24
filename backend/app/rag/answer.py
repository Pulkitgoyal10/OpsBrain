"""Answer synthesis: retrieve chunks, build a grounded prompt, call the LLM.

Per CLAUDE.md B4 step 3: answer only from provided context, cite every claim
with [source, page], and say so explicitly if the context doesn't have it.

F4: if the question references a known equipment tag, a direct Neo4j lookup
(app.rag.graph_lookup) is injected as an authoritative fact alongside the
vector-retrieved chunks - see graph_lookup.py's module docstring for why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.graph_lookup import (
    EquipmentNeighborhood,
    find_equipment_tags,
    format_graph_fact,
    lookup_equipment_fact,
)
from app.rag.groq_client import chat
from app.rag.retrieval import DEFAULT_TOP_K, RetrievedChunk, search_chunks

SYSTEM_PROMPT = (
    "You are OpsBrain, an industrial knowledge assistant. Answer the user's "
    "question using ONLY the information in the provided document excerpts. "
    "Do not use outside knowledge and do not guess. If the excerpts do not "
    "contain a clear answer, say so explicitly instead of speculating. "
    "If the question itself is not about industrial equipment, maintenance, "
    "safety, or regulatory topics at all - general knowledge, small talk, "
    "or an unrelated subject like geography or trivia - do not attempt to "
    "answer it, even if you know the answer from outside knowledge. "
    "Respond with exactly this sentence and nothing else: "
    '"I can only answer questions about your uploaded documents." '
    "This is different from a question that IS about that domain but whose "
    "specific answer is simply missing from the excerpts you were given "
    "(e.g. the excerpts don't happen to mention the value asked about) - "
    "that case is NOT off-topic, and must never trigger the sentence above. "
    "For that case, say plainly that the excerpts don't state that "
    "specific fact, still following the citation rules below for anything "
    "you do state. Never combine the off-topic sentence with any other "
    "text in the same answer - it is always the entire response by itself, "
    "or not used at all. "
    "If a specific value (a date, quantity, ID, or similar) is directly and "
    "literally stated in an excerpt, you must use that literal value. Never "
    "compute, estimate, or extrapolate a value from patterns across other "
    "rows or entities, even if those patterns seem consistent - only report "
    "what is explicitly written for the exact thing being asked about. Never "
    "mention a date, number, or ID that does not appear verbatim in the "
    "excerpts - not even as a wrong guess you then correct. State the "
    "correct literal value immediately and directly, with no self-correction "
    "narration. "
    "Answer in 1-2 sentences using the literal stated value, then stop. "
    "Cite every claim inline in exactly this format: [source_filename, page N]. "
    "The citation must point to the exact excerpt that contains the literal "
    "value you stated - not any excerpt that merely mentions the same "
    "equipment, entity, or topic. If the value and a mention of the entity "
    "appear in different excerpts, cite the excerpt with the value. "
    'For example: "The flash point is not flammable [Sample SDS Handout.pdf, page 6]."\n'
    "An excerpt labeled 'VERIFIED GRAPH FACT' is ground truth extracted directly "
    "from structured data, not free text - treat it as fully authoritative for "
    "the equipment tag and fields it covers, and cite it the same way as any "
    "other excerpt, using its bracketed [filename, page N]. Some excerpts (e.g. "
    "spreadsheet rows) have no page number - those are labeled [filename] with "
    "no comma or page; cite them exactly that way, without inventing a page."
)

# Matches citations in the "[filename, page N]" format requested in
# SYSTEM_PROMPT, tolerating "p."/"page" and either bracket style - and also
# the pageless "[filename]" form used for non-paginated sources (e.g.
# spreadsheet rows, which carry page_number=None per the ChunkMetadata
# contract). The page group is optional; group(2) is None when it's absent.
_CITATION_PATTERN = re.compile(
    r"[\[\(]\s*([^,\[\]\(\)]+?)\s*(?:,\s*(?:page|p\.?)\s*(\d+))?\s*[\]\)]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Citation:
    source_filename: str
    page_number: int | None


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    citations: list[Citation]
    # Citations that resolve to a graph fact rather than a vector chunk. Drives
    # CLAUDE.md F-B's "graph context used" strip - empty when no equipment tag
    # was detected, or the tag had no graph match, or the model didn't end up
    # citing the fact it was given.
    graph_citations: list[Citation]
    # The actual structured fact data retrieved from the graph and given to
    # the LLM as context this turn (CLAUDE.md B4's graph_context[]) - not
    # filtered to what the model ended up citing, unlike graph_citations.
    # Complementary signals: this is "what graph context was available",
    # graph_citations is "what graph context was actually used in the answer".
    graph_context: list[EquipmentNeighborhood]


def _citation_bracket(source_filename: str, page_number: int | None) -> str:
    """[filename, page N] for a paginated source, or [filename] when
    page_number is None (e.g. a spreadsheet row - no pages to cite).
    """
    if page_number is None:
        return f"[{source_filename}]"
    return f"[{source_filename}, page {page_number}]"


def _build_context(chunks: list[RetrievedChunk]) -> str:
    blocks = [
        f"{_citation_bracket(c.source_filename, c.page_number)}\n{c.text}" for c in chunks
    ]
    return "\n\n---\n\n".join(blocks)


def _extract_citations(answer_text: str, known: set[tuple[str, int | None]]) -> list[Citation]:
    """Only count a citation if it matches an entry in `known` - this filters
    out anything the model cites that wasn't actually in its context.
    """
    seen: set[tuple[str, int | None]] = set()
    citations: list[Citation] = []

    for filename, page_str in _CITATION_PATTERN.findall(answer_text):
        key = (filename.strip(), int(page_str) if page_str else None)
        if key in known and key not in seen:
            seen.add(key)
            citations.append(Citation(source_filename=key[0], page_number=key[1]))

    return citations


def _citations_in_neighborhood(neighborhood: EquipmentNeighborhood) -> set[tuple[str, int | None]]:
    """Every (filename, page) pair citable from this equipment's 1-2 hop
    neighborhood - the maintenance event plus each substance and the
    location, not just a single fixed citation.
    """
    known: set[tuple[str, int | None]] = set()
    if neighborhood.maintenance_event:
        m = neighborhood.maintenance_event
        known.add((m.source_document, m.page_number))
    for s in neighborhood.substances:
        if s.citation:
            known.add(s.citation)
    if neighborhood.location and neighborhood.location.citation:
        known.add(neighborhood.location.citation)
    return known


def answer_question(
    question: str, *, top_k: int = DEFAULT_TOP_K, document: str | None = None
) -> AnswerResult:
    neighborhoods = [
        n
        for n in (
            lookup_equipment_fact(tag, document=document) for tag in find_equipment_tags(question)
        )
        if n is not None
    ]
    chunks = search_chunks(question, top_k=top_k, document=document)

    context_blocks = [format_graph_fact(n) for n in neighborhoods]
    context_blocks.append(_build_context(chunks))
    context = "\n\n---\n\n".join(block for block in context_blocks if block)

    user_prompt = f"Document excerpts:\n\n{context}\n\nQuestion: {question}"

    answer_text = chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        # Raised from 150: a relational question can now pull a multi-part
        # neighborhood (maintenance + several substances + regulations +
        # location), and 150 was tuned for a single-fact answer - risked
        # truncating a legitimately longer grounded answer mid-sentence.
        max_tokens=300,
        frequency_penalty=0.3,
    )

    chunk_known: set[tuple[str, int | None]] = {(c.source_filename, c.page_number) for c in chunks}
    graph_known: set[tuple[str, int | None]] = set()
    for n in neighborhoods:
        graph_known |= _citations_in_neighborhood(n)

    citations = _extract_citations(answer_text, chunk_known)
    graph_citations = _extract_citations(answer_text, graph_known)

    return AnswerResult(
        answer=answer_text,
        citations=citations,
        graph_citations=graph_citations,
        graph_context=neighborhoods,
    )
