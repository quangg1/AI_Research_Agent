# Fine-tune vs RAG vs long context for new facts

Source: Applied LLM systems notes.
URL: https://huggingface.co/docs/transformers/en/model_doc/rag
Published: 2025
Credibility: official_regulation

Fine-tuning is a poor default for facts that change weekly. Weights memorize training snapshots; they do not subscribe to a changelog. RAG and tool use are the usual way to inject new documents.

Fine-tuning still wins for style, tool-call format, domain language, and latency (shorter prompts). It is not "always better than RAG."

Long context can replace retrieval for small corpora if the working set fits and you can afford the prefill cost. Prefill scales with tokens; a 128k window on every query is often more expensive than hybrid retrieval plus a 4k generation.

A practical split: RAG for facts, light SFT/DPO for format, long context for the retrieved pack — not one pattern for every job.
