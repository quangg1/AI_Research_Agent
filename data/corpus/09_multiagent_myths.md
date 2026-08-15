# Multi-agent does not always beat a single agent with tools

Source: Multi-agent evaluation notes.
URL: https://arxiv.org/abs/2308.08155
Published: 2025
Credibility: peer_reviewed

Giving each role its own LLM does not automatically beat one agent with the same tools and a better planner. Extra agents duplicate context, inflate tokens, and create coordination failures.

Multi-agent helps when roles have genuinely different tools, different memory, or a human gate between them (researcher vs critic vs writer). It hurts when they are three copies of the same prompt.

Papers that claim large multi-agent gains often change the tool budget or the number of samples at the same time. Hold tools and tokens constant before you celebrate the architecture.

Kiln's critic loop is a single graph with roles, not a swarm.
