"""A small LangGraph workflow without automatic side-effect retries."""

from langgraph.graph import END, START, StateGraph

from .nodes import AgentNodes
from .state import AgentState


def build_graph(nodes: AgentNodes, checkpointer):
    graph = StateGraph(AgentState)
    for name in ("context", "plan", "validate", "compile", "execute", "preview", "await_approval"):
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "context")
    graph.add_edge("context", "plan")
    graph.add_conditional_edges("plan", lambda state: "end" if state.get("response") else "validate",
                                {"end": END, "validate": "validate"})
    graph.add_conditional_edges("validate", lambda state: "end" if state.get("response") else "compile",
                                {"end": END, "compile": "compile"})
    graph.add_edge("compile", "execute")
    graph.add_edge("execute", "preview")
    graph.add_conditional_edges("preview", lambda state: "wait" if state["response"]["status"] == "awaiting_approval" else "end",
                                {"wait": "await_approval", "end": END})
    graph.add_edge("await_approval", END)
    return graph.compile(checkpointer=checkpointer)
