from app.llm.client import LLMClient


def test_generate_without_client_is_empty():
    llm = LLMClient.__new__(LLMClient)
    llm.model = "test"
    llm._client = None
    llm.last_tokens = 0
    llm.mode = "heuristic"
    assert llm.generate("hello") == ""
