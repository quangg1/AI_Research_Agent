from contextlib import asynccontextmanager
from types import SimpleNamespace

from app import runtime


class _Connection:
    async def execute(self, _statement, _params=None):
        return self


class _Pool:
    @asynccontextmanager
    async def connection(self):
        yield _Connection()


class _Graph:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.inputs = []

    async def aget_state(self, _config):
        return self.snapshot

    async def astream(self, graph_input, **_kwargs):
        self.inputs.append(graph_input)
        if False:
            yield {}


def _snapshot(values, interrupt=None, next_nodes=()):
    tasks = []
    if interrupt is not None:
        tasks = [SimpleNamespace(interrupts=[SimpleNamespace(value=interrupt)])]
    return SimpleNamespace(
        values=values,
        tasks=tasks,
        next=next_nodes,
        config={"configurable": {"checkpoint_id": "cp-1"}},
    )


async def test_terminal_checkpoint_is_returned_without_rerun(monkeypatch):
    graph = _Graph(_snapshot({"status": "completed", "report": {"title": "Done"}}))
    monkeypatch.setattr(runtime, "_pool", _Pool())
    monkeypatch.setattr(runtime, "_graph", graph)

    frames = [
        frame
        async for frame in runtime.stream_execution(
            {
                "kind": "start",
                "runId": "run-1",
                "executionId": "exec-1",
                "query": "Explain continuous batching in serving systems",
            }
        )
    ]

    assert [frame["type"] for frame in frames] == ["terminal"]
    assert [frame["sequence"] for frame in frames] == [0]
    assert graph.inputs == []


async def test_start_retry_at_interrupt_reemits_interrupt(monkeypatch):
    graph = _Graph(
        _snapshot(
            {"status": "researching", "last_execution_id": "exec-1"},
            interrupt={"type": "approve_report"},
            next_nodes=("hitl",),
        )
    )
    monkeypatch.setattr(runtime, "_pool", _Pool())
    monkeypatch.setattr(runtime, "_graph", graph)

    frames = [
        frame
        async for frame in runtime.stream_execution(
            {
                "kind": "start",
                "runId": "run-1",
                "executionId": "exec-2",
                "query": "Explain continuous batching in serving systems",
            }
        )
    ]

    assert frames[0]["type"] == "interrupt"
    assert frames[0]["sequence"] == 0
    assert frames[0]["data"]["type"] == "approve_report"
    assert graph.inputs == []


async def test_same_resume_execution_continues_with_none(monkeypatch):
    graph = _Graph(
        _snapshot(
            {"status": "researching", "last_execution_id": "exec-1"},
            interrupt={"type": "approve_report"},
            next_nodes=("hitl",),
        )
    )
    monkeypatch.setattr(runtime, "_pool", _Pool())
    monkeypatch.setattr(runtime, "_graph", graph)

    frames = [
        frame
        async for frame in runtime.stream_execution(
            {
                "kind": "resume",
                "runId": "run-1",
                "executionId": "exec-1",
                "decision": {"action": "approve"},
            }
        )
    ]

    assert graph.inputs == [None]
    assert frames[-1]["type"] == "interrupt"
