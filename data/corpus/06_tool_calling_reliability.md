# Tool calling and structured output reliability

Source: OpenAI / Anthropic tool-use docs.
URL: https://platform.openai.com/docs/guides/function-calling
Published: 2025
Credibility: official_regulation

Constrained decoding and JSON-schema tool calling raise format reliability versus "please return JSON." They do not make the model choose the right tool or the right arguments.

Retries, schema validation, and idempotent tools matter more than swapping models when the failure mode is malformed calls. Log tool name, args, latency, and error class.

Function-calling evals that only score JSON parse rate overstate production readiness. Measure argument correctness against a golden tool trace.

Parallel tool calls help independent lookups and hurt when later tools depend on earlier results — sequence those explicitly in the graph.
