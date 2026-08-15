# Continuous batching, KV cache, and serving throughput

Source: vLLM documentation.
URL: https://arxiv.org/abs/2309.06180
Published: 2025
Credibility: standard_body

vLLM-style engines raise GPU utilization with PagedAttention (non-contiguous KV cache) and continuous batching: new requests join a running batch instead of waiting for a static batch to drain.

Throughput and latency trade off. Aggressive batching improves tokens/sec and hurts time-to-first-token under load. Prefix caching helps multi-turn and RAG prompts that share a long system prefix.

Quantization (FP8, INT4/AWQ/GPTQ) cuts memory and can raise concurrency. Quality drop is task-dependent; measure on your eval set, not a blog table.

A bigger model is not always faster-or-better once you account for batch size, quantization, and the fact that 70B FP16 may serve fewer concurrent users than 8B FP8 on the same GPU.
