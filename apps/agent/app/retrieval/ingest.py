"""Ingest local LLM-systems corpus into memory + Qdrant."""

from app.retrieval.store import ingest_corpus

if __name__ == "__main__":
    n = ingest_corpus()
    print(f"ingested {n} chunks")
