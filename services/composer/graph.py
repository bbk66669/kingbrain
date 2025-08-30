import os, time
from typing import Dict, Any, List
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from .artifacts import bundle
from .agents import load_agents, get_agent, call_llm
from .nodes import architect as N_arch
from .nodes import builder as N_build
from .nodes import reviewer as N_review

# —— LangGraph 的轻量 State（我们只传少量键） ——————————————
class State(dict):
    ...

def _llm_try_summarize(phase: str, task: str, notes: str, agent_cfg: Dict[str, Any]) -> str:
    """有 key 就让 LLM 生成一段摘要；失败即返回空字符串"""
    prov  = (agent_cfg.get("provider") or "").lower()
    model = agent_cfg.get("model") or ""
    sys   = agent_cfg.get("system_prompt") or ""
    user  = f"Phase={phase}\nTask={task}\nNotes={notes}\nPlease produce a short planning summary."
    max_t = int(agent_cfg.get("max_tokens") or 800)
    temp  = float(agent_cfg.get("temperature") or 0.2)
    return call_llm(prov, model, sys, user, max_tokens=max_t, temperature=temp) or ""

def _node_architect(state: State) -> State:
    ctx  = state["context"]; task = state["task"]; notes = state["notes"]
    agents = state["agents"]; a = get_agent(agents, "architect")
    arts = N_arch.run(task, notes, ctx, a)

    # 若 LLM 有返回，在 PLAN.md 末尾附加“LLM Summary”
    summary = _llm_try_summarize("PLAN", task, notes, a)
    if summary:
        for it in arts:
            if it["relpath"] == "docs/kingbrain/PLAN/PLAN.md" and it["encoding"] == "utf-8":
                it["content"] = str(it["content"]) + "\n## LLM Summary\n" + summary + "\n"

    return {"artifacts": arts}

def _node_borrow(state: State) -> State:
    ctx  = state["context"]; task = state["task"]; notes = state["notes"]
    agents = state["agents"]; b = get_agent(agents, "builder")
    arts = N_build.run_borrow(task, notes, ctx, b)
    return {"artifacts": arts}

def _node_diff(state: State) -> State:
    ctx  = state["context"]; task = state["task"]; notes = state["notes"]
    agents = state["agents"]; b = get_agent(agents, "builder")
    arts = N_build.run_diff(task, notes, ctx, b)
    return {"artifacts": arts}

def _node_cr(state: State) -> State:
    ctx  = state["context"]; task = state["task"]; notes = state["notes"]
    agents = state["agents"]; r = get_agent(agents, "reviewer")
    arts = N_review.run_cr(task, notes, ctx, r)
    return {"artifacts": arts}

# —— 编译一个最小图（虽然每次只跑一个节点） ———————————
def _compile_graph():
    memory = MemorySaver()
    g = StateGraph(State)
    g.add_node("architect", _node_architect)
    g.add_node("borrow", _node_borrow)
    g.add_node("diff", _node_diff)
    g.add_node("cr", _node_cr)
    # 为了符合 LangGraph API 要求，给一个默认链
    g.add_edge(START, "architect")
    g.add_edge("architect", END)
    return g.compile(checkpointer=memory)

_GRAPH = _compile_graph()

def run_graph(phase: str, task: str, notes: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    外部唯一入口：
      - phase: "ACK" | "PLAN" | "BORROW" | "DIFF" | "CR"
      - 返回 {"artifacts":[...]}，每个 artifact = {relpath, kind, encoding, content}
    说明：
      - ACK 阶段目前不生成文件（可扩展为 POR-ACK）
      - CR 输出放在 /reports/CR/（符合你的 allowlist）
    """
    repo_root = context.get("repo_root") or os.getenv("REPO_ROOT", "/workspace")
    agents = load_agents(repo_root)

    state: State = {
        "phase": phase.upper(),
        "task": task or "",
        "notes": notes or "",
        "context": context or {},
        "agents": agents,
    }

    # 每个阶段只跑对应节点；LangGraph 主要提供一致的编排能力/状态留存。
    if state["phase"] == "PLAN":
        out = _GRAPH.invoke(state, start_at="architect")
        return bundle(out.get("artifacts", []))
    if state["phase"] == "BORROW":
        out = _node_borrow(state)     # 单节点
        return bundle(out.get("artifacts", []))
    if state["phase"] == "DIFF":
        out = _node_diff(state)
        return bundle(out.get("artifacts", []))
    if state["phase"] == "CR":
        out = _node_cr(state)
        return bundle(out.get("artifacts", []))
    if state["phase"] == "ACK":
        return bundle([])             # 暂无产物
    # 未知阶段 → 空
    return bundle([])
