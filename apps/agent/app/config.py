from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../../.env"), extra="ignore")

    google_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    gemini_embed_model: str = "text-embedding-004"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    xai_api_key: str = ""
    grok_api_key: str = ""
    grok_model: str = "grok-4.6"
    tavily_api_key: str = ""

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "kiln"
    postgres_user: str = "kiln"
    postgres_password: str = "kiln_dev_password"
    database_url: str = "postgresql://kiln:kiln_dev_password@localhost:5432/kiln"
    persistence_required: bool = True
    knowledge_backend: str = "postgres"
    knowledge_import_path: str = ""

    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "kiln_evidence"

    max_tool_calls: int = 52  # legacy env; pools set by configure_budget_pools("deep")
    max_input_tokens: int = 80_000
    max_iterations: int = 6

    # Showcase / benchmark runs: higher pools so coverage gate rarely stops on budget.
    showcase_mode: bool = False
    showcase_retrieval_pool: int = 48
    showcase_enrich_pool: int = 36
    showcase_max_iterations: int = 10
    showcase_reserve_calls: int = 3

    # Answer reuse: how close a past question must be before its memo is reused.
    knowledge_enabled: bool = True
    knowledge_reuse_similarity: float = 0.88
    knowledge_augment_similarity: float = 0.70
    knowledge_fresh_days: int = 30
    knowledge_max_records: int = 400

    corpus_dir: str = "../../data/corpus"
    eval_path: str = "../../data/eval/golden_set.json"

    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    agent_shared_key: str = ""
    app_env: str = "development"

    langchain_api_key: str = ""
    langchain_tracing_v2: bool = False
    langchain_project: str = "kiln"

    # When true, research runs must send a caller LLM key. Server keys are not used.
    llm_byok_required: bool = False

    def grok_secret(self) -> str:
        return (self.xai_api_key or self.grok_api_key).strip()

    def platform_key(self, provider: str) -> str:
        name = (provider or "").strip().lower()
        if name == "gemini":
            return self.google_api_key.strip()
        if name == "openai":
            return self.openai_api_key.strip()
        if name == "grok":
            return self.grok_secret()
        return ""

    def tavily_keys(self) -> list[str]:
        from app.llm.providers import split_api_keys

        return split_api_keys(self.tavily_api_key)

    def platform_keys(self, provider: str) -> list[str]:
        from app.llm.providers import split_api_keys

        return split_api_keys(self.platform_key(provider))

    def platform_configured(self) -> dict[str, bool]:
        return {
            "gemini": bool(self.platform_keys("gemini")),
            "openai": bool(self.platform_keys("openai")),
            "grok": bool(self.platform_keys("grok")),
        }

    def require_byok(self) -> bool:
        return bool(self.llm_byok_required)

    def agent_key(self) -> str:
        return (self.agent_shared_key or "").strip()

    def qdrant_enabled(self) -> bool:
        return bool(self.qdrant_url.strip())


settings = Settings()
