# Embeddings, chunking, and cross-encoder rerank

Source: Retrieval systems notes.
URL: https://huggingface.co/blog/how-to-train-sentence-transformers
Published: 2025
Credibility: official_regulation

Chunk size and overlap dominate RAG quality as much as the embedding model. 200–400 token chunks with headings as prefixes beat 2k-token blobs for factual lookup.

A cheap first-stage retriever (BM25 or bi-encoder) plus a cross-encoder reranker on the top 20–50 is the standard quality jump. Skipping rerank and "just using a larger embedding" is a common miss.

Re-embedding the whole corpus on every model upgrade is optional if you keep a lexical index. Hybrid fusion lets you migrate dense models gradually.

Hash embeddings are a dev fallback. They are not a production dense index.
