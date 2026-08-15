import pytest

from app.graph.builder import build_graph, build_test_graph


def test_graph_compiles_with_and_without_hitl():
    assert build_test_graph(enable_hitl=True)
    assert build_test_graph(enable_hitl=False)


def test_production_graph_requires_explicit_checkpointer():
    with pytest.raises(RuntimeError, match="checkpointer"):
        build_graph()
