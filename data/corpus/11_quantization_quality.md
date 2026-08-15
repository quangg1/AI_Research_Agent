# Quantization tradeoffs: memory, quality, concurrency

Source: Hugging Face / NVIDIA quantization notes.
URL: https://huggingface.co/docs/transformers/en/quantization/overview
Published: 2025
Credibility: official_regulation

INT4/AWQ/GPTQ and FP8 reduce weights and KV memory so more concurrent sequences fit on one GPU. Perplexity deltas look small; task deltas on tool calling and long RAG packs can be larger.

Do not pick a bit-width from a tweet. Run the product eval (golden claims, tool traces) at each precision.

KV-cache quantization and weight quantization are different knobs. Mixing both can compound errors on long contexts.

An 8B FP8 model with continuous batching can beat a 70B FP16 model on user-visible latency at the same GPU spend even if the 70B wins a chat arena.
