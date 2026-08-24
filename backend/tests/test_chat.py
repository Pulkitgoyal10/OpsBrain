"""POST /chat."""

from __future__ import annotations

from app.rag.retrieval import RetrievedChunk


def test_chat_rejects_empty_question(client):
    response = client.post("/chat", json={"question": "   "})

    assert response.status_code == 400


def test_chat_returns_answer_with_citation_matching_retrieval(
    client, mock_search_chunks, mock_lookup_equipment_fact, mock_groq_chat
):
    mock_search_chunks.return_value = [
        RetrievedChunk(
            text="The flash point is not flammable.",
            source_filename="Sample SDS Handout.pdf",
            page_number=6,
            chunk_id="Sample SDS Handout_p6_0",
            score=0.91,
        )
    ]
    mock_groq_chat.return_value = (
        "The flash point is not flammable [Sample SDS Handout.pdf, page 6]."
    )

    response = client.post("/chat", json={"question": "What is the flash point?"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "The flash point is not flammable [Sample SDS Handout.pdf, page 6]."
    # The citation returned to the caller must match the (filename, page) of
    # the chunk actually retrieved - not just any string the model happened
    # to output in brackets.
    assert body["citations"] == [{"source_filename": "Sample SDS Handout.pdf", "page_number": 6}]
    assert body["graph_citations"] == []
    assert body["graph_context"] == []


def test_chat_drops_citation_not_in_retrieved_chunks(
    client, mock_search_chunks, mock_lookup_equipment_fact, mock_groq_chat
):
    """A citation the model invents - pointing at a (filename, page) that was
    never actually retrieved - must not be surfaced to the caller as if it
    were grounded. Only citations matching a real chunk survive.
    """
    mock_search_chunks.return_value = [
        RetrievedChunk(
            text="The flash point is not flammable.",
            source_filename="Sample SDS Handout.pdf",
            page_number=6,
            chunk_id="Sample SDS Handout_p6_0",
            score=0.91,
        )
    ]
    mock_groq_chat.return_value = "The flash point is not flammable [Some Other Doc.pdf, page 1]."

    response = client.post("/chat", json={"question": "What is the flash point?"})

    assert response.status_code == 200
    assert response.json()["citations"] == []
