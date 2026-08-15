from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../../.env"), extra="ignore")

    google_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    gemini_embed_model: str = "text-embedding-004"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
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

    max_tool_calls: int = 12
    max_input_tokens: int = 80_000
    max_iterations: int = 3

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


settings = Settings()
