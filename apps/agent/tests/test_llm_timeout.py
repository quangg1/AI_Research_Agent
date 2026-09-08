from app.llm.client import LLMClient


def test_generate_without_client_is_empty():
    # A properly-constructed client with no keys/slots must degrade to "" rather
    # than raise. Construct via the real __init__ (use_env=False → no slots) so the
    # test exercises the actual contract instead of a hand-mocked instance.
    llm = LLMClient(use_env=False)
    assert not llm.available
    assert llm.generate("hello") == ""
