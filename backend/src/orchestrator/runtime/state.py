from typing import Annotated, Any

from langchain.agents.middleware import AgentState


def merge_node_outputs(
    left: dict[str, Any] | None, right: dict[str, Any] | None
) -> dict[str, Any]:
    """
    Conflict-safe reducer for node outputs.
    Rejects duplicate writes for the same node in one superstep.
    Merges node outputs keyed by node ID.
    """
    merged = dict(left or {})
    if not right:
        return merged

    step = right.get("__step__")
    node = right.get("__node__")

    if step is not None:
        last_step = merged.get("__step__")
        step_nodes = set(merged.get("__step_nodes__", []))
        if last_step != step:
            step_nodes = set()
            merged["__step__"] = step
        if node:
            if node in step_nodes:
                raise ValueError(
                    f"Duplicate write for node '{node}' in superstep {step}"
                )
            step_nodes.add(node)
        merged["__step_nodes__"] = list(step_nodes)

    for k, v in right.items():
        if not k.startswith("__"):
            merged[k] = v
    return merged


def merge_usage(
    left: dict[str, Any] | None, right: dict[str, Any] | None
) -> dict[str, Any]:
    """
    Merge token usage metrics across supersteps and parallel nodes.
    """
    merged = dict(left or {})
    if not right:
        return merged
    for k, v in right.items():
        if isinstance(v, (int, float)):
            merged[k] = merged.get(k, 0) + v
        elif isinstance(v, dict) and isinstance(merged.get(k), dict):
            nested = dict(merged[k])
            for nk, nv in v.items():
                if isinstance(nv, (int, float)):
                    nested[nk] = nested.get(nk, 0) + nv
                else:
                    nested[nk] = nv
            merged[k] = nested
        else:
            merged[k] = v
    return merged


class RuntimeState(AgentState[Any], total=False):
    input: dict[str, Any]
    outputs: Annotated[dict[str, Any], merge_node_outputs]
    result_name: str | None
    run_id: str
    usage: Annotated[dict[str, Any], merge_usage]
