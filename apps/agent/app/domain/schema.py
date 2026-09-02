from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class QueryType(str, Enum):
    FACTUAL = "factual"
    COMPARISON = "comparison"
    OPEN_RESEARCH = "open_research"


class AgentName(str, Enum):
    SEARCH = "search"
    SCHOLAR = "scholar"
    DOCS = "docs"


class SourceTier(str, Enum):
    OFFICIAL_REGULATION = "official_regulation"  # primary vendor / framework docs
    INTERGOVERNMENTAL = "intergovernmental"  # eval labs / academic institutes
    STANDARD_BODY = "standard_body"  # serving / ML framework docs
    PEER_REVIEWED = "peer_reviewed"
    SPECIALIST_RESEARCH = "specialist_research"
    INDUSTRY_ASSOCIATION = "industry_association"
    NEWS_ANALYSIS = "news_analysis"
    VENDOR_OR_CONSULTANCY = "vendor_or_consultancy"
    UNKNOWN = "unknown"


TIER_SCORE = {
    SourceTier.OFFICIAL_REGULATION: 0.96,
    SourceTier.INTERGOVERNMENTAL: 0.90,
    SourceTier.STANDARD_BODY: 0.88,
    SourceTier.PEER_REVIEWED: 0.84,
    SourceTier.SPECIALIST_RESEARCH: 0.76,
    SourceTier.INDUSTRY_ASSOCIATION: 0.62,
    SourceTier.NEWS_ANALYSIS: 0.58,
    SourceTier.VENDOR_OR_CONSULTANCY: 0.42,
    SourceTier.UNKNOWN: 0.28,
}

TIER_LABEL = {
    SourceTier.OFFICIAL_REGULATION: "Primary docs",
    SourceTier.INTERGOVERNMENTAL: "Eval lab",
    SourceTier.STANDARD_BODY: "Framework",
    SourceTier.PEER_REVIEWED: "Peer reviewed",
    SourceTier.SPECIALIST_RESEARCH: "Specialist",
    SourceTier.INDUSTRY_ASSOCIATION: "Industry",
    SourceTier.NEWS_ANALYSIS: "Analysis",
    SourceTier.VENDOR_OR_CONSULTANCY: "Vendor",
    SourceTier.UNKNOWN: "Unknown",
}


HOST_TIER: dict[str, SourceTier] = {
    "openai.com": SourceTier.OFFICIAL_REGULATION,
    "platform.openai.com": SourceTier.OFFICIAL_REGULATION,
    "docs.anthropic.com": SourceTier.OFFICIAL_REGULATION,
    "anthropic.com": SourceTier.OFFICIAL_REGULATION,
    "ai.google.dev": SourceTier.OFFICIAL_REGULATION,
    "ai.google.com": SourceTier.OFFICIAL_REGULATION,
    "cloud.google.com": SourceTier.OFFICIAL_REGULATION,
    "huggingface.co": SourceTier.OFFICIAL_REGULATION,  # refined further in credibility.tier_for by path
    "github.com": SourceTier.SPECIALIST_RESEARCH,
    "gitlab.com": SourceTier.SPECIALIST_RESEARCH,
    "medium.com": SourceTier.NEWS_ANALYSIS,
    "towardsai.net": SourceTier.NEWS_ANALYSIS,
    "pub.towardsai.net": SourceTier.NEWS_ANALYSIS,
    "emergentmind.com": SourceTier.NEWS_ANALYSIS,
    "docs.vllm.ai": SourceTier.STANDARD_BODY,
    "vllm.ai": SourceTier.STANDARD_BODY,
    "pytorch.org": SourceTier.STANDARD_BODY,
    "docs.pytorch.org": SourceTier.STANDARD_BODY,
    "langchain.com": SourceTier.STANDARD_BODY,
    "python.langchain.com": SourceTier.STANDARD_BODY,
    "docs.smith.langchain.com": SourceTier.STANDARD_BODY,
    "langchain-ai.github.io": SourceTier.STANDARD_BODY,
    "docs.llamaindex.ai": SourceTier.STANDARD_BODY,
    "nvidia.com": SourceTier.STANDARD_BODY,
    "docs.nvidia.com": SourceTier.STANDARD_BODY,
    "arxiv.org": SourceTier.SPECIALIST_RESEARCH,  # ArXiv is preprint server, not peer-reviewed
    "openalex.org": SourceTier.PEER_REVIEWED,
    "semanticscholar.org": SourceTier.PEER_REVIEWED,
    "doi.org": SourceTier.PEER_REVIEWED,
    "aclanthology.org": SourceTier.PEER_REVIEWED,
    "crfm.stanford.edu": SourceTier.INTERGOVERNMENTAL,
    "helm.stanford.edu": SourceTier.INTERGOVERNMENTAL,
    "hai.stanford.edu": SourceTier.INTERGOVERNMENTAL,
    "bair.berkeley.edu": SourceTier.SPECIALIST_RESEARCH,
    "eleuther.ai": SourceTier.SPECIALIST_RESEARCH,
    "together.ai": SourceTier.SPECIALIST_RESEARCH,
    "lmsys.org": SourceTier.SPECIALIST_RESEARCH,
    "arena.lmsys.org": SourceTier.SPECIALIST_RESEARCH,
    "semianalysis.com": SourceTier.NEWS_ANALYSIS,
    "thebatch.com": SourceTier.NEWS_ANALYSIS,
    "deeplearning.ai": SourceTier.NEWS_ANALYSIS,
}

ALLOWED_DOC_HOSTS = {
    "openai.com",
    "platform.openai.com",
    "docs.anthropic.com",
    "anthropic.com",
    "ai.google.dev",
    "huggingface.co",
    "github.com",
    "gitlab.com",
    "docs.vllm.ai",
    "vllm.ai",
    "pytorch.org",
    "docs.pytorch.org",
    "langchain.com",
    "python.langchain.com",
    "docs.smith.langchain.com",
    "langchain-ai.github.io",
    "arxiv.org",
    "crfm.stanford.edu",
    "helm.stanford.edu",
    "hai.stanford.edu",
    "nvidia.com",
    "docs.nvidia.com",
    "eleuther.ai",
    "lmsys.org",
}

DOMAIN_SCOPE = (
    "Applied AI and LLM systems decisions: model selection and serving "
    "(latency, throughput, quantization, vLLM/TGI), RAG architecture "
    "(chunking, hybrid search, rerank, when not to use a vector DB), "
    "agent orchestration (loops, tool calling, HITL), eval and observability "
    "(golden sets, LLM-as-judge pitfalls, tracing), fine-tune vs prompt vs RAG "
    "vs long-context tradeoffs, benchmark caveats, and prompt-injection as a "
    "product risk. Out of scope: generic app debugging, medical advice, "
    "crypto trading, and lifestyle tips."
)


class SubQuery(BaseModel):
    agent: AgentName
    question: str
    rationale: str = ""


class Plan(BaseModel):
    query_type: QueryType
    goal: str
    sub_queries: list[SubQuery] = Field(default_factory=list)
    agents_to_run: list[AgentName] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    id: str
    title: str
    url: str = ""
    snippet: str
    source_agent: AgentName
    tier: SourceTier = SourceTier.UNKNOWN
    credibility: float = 0.28
    published: str = ""
    quote: str = ""


class Claim(BaseModel):
    id: str
    text: str
    quote: str = ""
    url: str = ""
    tier: str = ""
    support_ids: list[str] = Field(default_factory=list)
    contradict_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    caveats: list[str] = Field(default_factory=list)
    grounded: bool = False
    slot_id: str = ""
    priority: str = ""
    status: str = ""
    evidence_type: str = ""
    directness: str = ""
    kind: str = ""  # direct | derived | inferred | recommendation | speculative
    published: str = ""
    locator: str = ""  # section / table / experiment if the source states it
    quality_band: str = ""
    provenance: str = ""  # measured | author_assumption | secondhand | unknown
    verification_status: str = ""  # quote-matched statuses: verified | unsupported | ...
    verification_note: str = ""


class CriticVerdict(BaseModel):
    status: str = "insufficient"  # sufficient | insufficient | contradicted
    gate_reason: str = ""  # sufficient | insufficient_coverage | insufficient_budget | contradicted
    coverage_gate: dict = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    followup_queries: list[SubQuery] = Field(default_factory=list)
    claimed_unsupported: list[str] = Field(default_factory=list)
    confidence_floor: float = 0.0
    coverage: dict = Field(default_factory=dict)
    depth_score: dict = Field(default_factory=dict)


class Budget(BaseModel):
    max_tool_calls: int = 24
    max_retrieval_calls: int = 28
    max_enrich_calls: int = 20
    max_tokens: int = 80_000
    max_iterations: int = 5
    used_tool_calls: int = 0
    used_retrieval_calls: int = 0
    used_enrich_calls: int = 0
    used_tokens: int = 0
    iterations: int = 0

    def sync_totals(self) -> None:
        self.used_tool_calls = self.used_retrieval_calls + self.used_enrich_calls

    @property
    def remaining_retrieval_calls(self) -> int:
        return max(0, self.max_retrieval_calls - self.used_retrieval_calls)

    @property
    def remaining_enrich_calls(self) -> int:
        return max(0, self.max_enrich_calls - self.used_enrich_calls)

    @property
    def remaining_calls(self) -> int:
        """Search/scholar/planner loops use the retrieval pool."""
        return self.remaining_retrieval_calls

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens - self.used_tokens)

    @property
    def remaining_iterations(self) -> int:
        return max(0, self.max_iterations - self.iterations)

    @property
    def exhausted(self) -> bool:
        return (
            (self.remaining_retrieval_calls <= 0 and self.remaining_enrich_calls <= 0)
            or self.remaining_tokens <= 0
            or self.remaining_iterations <= 0
        )


class HumanDecision(BaseModel):
    action: str = "approve"  # approve | revise | start | cancel
    notes: str = ""
    extra_questions: list[str] = Field(default_factory=list)
    brief: dict = Field(default_factory=dict)


class ResearchBrief(BaseModel):
    """Editable Deep Research brief shown before the agent starts searching."""

    goal: str = ""
    query_type: str = QueryType.OPEN_RESEARCH.value
    sector: str = ""
    geography: str = ""
    time_horizon: str = "2025–2026"
    decision_type: str = ""
    constraints: list[str] = Field(default_factory=list)
    must_cover: list[str] = Field(default_factory=list)
    must_answer: list[dict] = Field(default_factory=list)
    sources_priority: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    deliverable: str = "Cited decision memo with claims, quotes, and contradictions"
    depth: str = "deep"  # quick | standard | deep (pipeline forces deep)
    assumptions: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    subquestions: list[str] = Field(default_factory=list)


class CitationRef(BaseModel):
    n: int
    evidence_id: str
    title: str = ""
    url: str = ""
    quote: str = ""
    tier: str = ""
    host: str = ""


class Report(BaseModel):
    title: str
    executive_summary: str
    body_markdown: str = ""
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    citations: list[CitationRef] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    method_notes: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    decision_rule: str = ""
    at_a_glance: str = ""
    metrics: dict = Field(default_factory=dict)
