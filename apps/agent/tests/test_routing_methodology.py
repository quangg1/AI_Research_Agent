from app.domain.research_intent import is_methodology_eval_query
from app.domain.routing_policy import agents_for
from app.domain.schema import AgentName, QueryType


def test_methodology_queries_prioritize_scholar():
    q = (
        "Analyze synthetic data generation methodologies and evaluation frameworks for "
        "high-quality data scarcity in specialized AI domains."
    )
    assert is_methodology_eval_query(q)
    agents = agents_for(QueryType.OPEN_RESEARCH, 12, query=q)
    assert agents[0] == AgentName.SCHOLAR
    assert AgentName.SCHOLAR in agents
