from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class ResumeDecision(ContractModel):
    action: str = "approve"
    notes: str = ""
    extra_questions: list[str] = Field(default_factory=list)
    brief: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] = Field(default_factory=dict)


class LlmKeys(ContractModel):
    gemini: SecretStr | None = None
    openai: SecretStr | None = None
    grok: SecretStr | None = None


class LlmCredential(ContractModel):
    provider: Literal["gemini", "openai", "grok"]
    api_key: SecretStr | None = Field(default=None, alias="apiKey")
    model: str | None = Field(default=None, max_length=200)
    keys: LlmKeys | None = None


class StartExecutionRequest(ContractModel):
    kind: Literal["start"]
    run_id: str = Field(alias="runId", min_length=1)
    execution_id: str = Field(alias="executionId", min_length=1)
    query: str = Field(min_length=8, max_length=4000)
    fresh: bool = False
    org_id: str | None = Field(default=None, alias="orgId", max_length=128)
    user_id: str | None = Field(default=None, alias="userId", max_length=128)
    llm: LlmCredential | None = None


class ResumeExecutionRequest(ContractModel):
    kind: Literal["resume"]
    run_id: str = Field(alias="runId", min_length=1)
    execution_id: str = Field(alias="executionId", min_length=1)
    decision: ResumeDecision
    org_id: str | None = Field(default=None, alias="orgId", max_length=128)
    user_id: str | None = Field(default=None, alias="userId", max_length=128)
    llm: LlmCredential | None = None


ExecutionRequest = Annotated[
    StartExecutionRequest | ResumeExecutionRequest,
    Field(discriminator="kind"),
]
execution_request_adapter = TypeAdapter(ExecutionRequest)


class ExecutionFrame(ContractModel):
    type: Literal["update", "heartbeat", "interrupt", "terminal", "error"]
    sequence: int = 0
    run_id: str = Field(alias="runId")
    execution_id: str = Field(alias="executionId")
    snapshot: dict[str, Any] = Field(default_factory=dict)
    data: Any = None
    interrupt: Any = None
    status: Literal["completed", "cancelled", "out_of_scope", "failed"] | None = None
    error: str | None = None
    retryable: bool | None = None
    current_node: str | None = Field(default=None, alias="current_node")
