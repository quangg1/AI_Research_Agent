# RAG does not always require a vector database

Source: Systems retrieval notes (BM25 / hybrid RAG).
URL: https://arxiv.org/abs/2005.11401
Published: 2024
Credibility: peer_reviewed

Lexical BM25 and hybrid retrieval (BM25 + dense) frequently match or beat dense-only RAG on domain corpora under a few million chunks. A vector database is an infrastructure choice, not a definition of RAG.

When the corpus is small, frequently updated, or dominated by exact identifiers (error codes, model names, API paths), sparse retrieval is often enough. Dense embeddings help paraphrases and poorly worded queries.

Vendor RAG decks that skip a BM25 baseline overstate the need for embeddings, ANN indexes, and re-embedding jobs.

Hybrid search with a cross-encoder reranker is the usual production pattern once recall plateaus — not "replace BM25 with vectors."
