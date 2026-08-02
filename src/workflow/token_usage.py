"""Task token and configured cost aggregation for workflow consumers."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
from typing import Any

from .pricing import decimal_string, price_usage_calls

TOKEN_KEYS = (
    "prompt_tokens", "completion_tokens", "total_tokens",
    "cached_tokens", "reasoning_tokens", "call_count",
)

ZERO_USAGE: dict[str, int] = {key: 0 for key in TOKEN_KEYS}


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _add_to_aggregate(target: dict[str, int], model_data: dict) -> None:
    for key in TOKEN_KEYS:
        target[key] = target.get(key, 0) + _safe_int(model_data.get(key, 0))


def _usage_from_call(call: dict[str, Any]) -> dict[str, int]:
    return {key: _safe_int(call.get(key, 0)) for key in TOKEN_KEYS}


def _normalize_call(
    call: dict[str, Any],
    *,
    node_id: str,
    agent_type: str,
    status: str,
    session_id: str,
) -> dict[str, Any]:
    result = dict(call)
    effective_agent_type = agent_type
    if effective_agent_type == "unknown":
        effective_agent_type = str(result.get("agent_type") or "unknown")
    model_id = str(result.get("model_id", ""))
    provider = str(result.get("provider", ""))
    model = str(result.get("model", ""))
    if not model_id and provider and model:
        model_id = f"{provider}:{model}"
    if model_id and ":" in model_id:
        inferred_provider, inferred_model = model_id.split(":", 1)
        provider = provider or inferred_provider
        model = model or inferred_model
    result.update({
        "provider": provider or "unknown",
        "model": model or model_id or "unknown",
        "model_id": model_id or "unknown",
        "node_id": node_id,
        "agent_type": effective_agent_type,
        "node_status": status,
        "session_id": str(result.get("session_id") or session_id),
    })
    for key in TOKEN_KEYS:
        result[key] = _safe_int(result.get(key, 0))
    if not result["call_count"]:
        result["call_count"] = 1
    return result


def _cost_summary(
    usage: dict[str, int],
    calls: list[dict[str, Any]],
    currency: str,
) -> dict[str, Any]:
    priced_calls = [call for call in calls if call.get("cost_status") == "priced"]
    unpriced_calls = [call for call in calls if call.get("cost_status") != "priced"]
    ledger_usage = dict(ZERO_USAGE)
    priced_usage = dict(ZERO_USAGE)
    unpriced_usage = dict(ZERO_USAGE)
    amount = Decimal(0)

    for call in calls:
        call_usage = _usage_from_call(call)
        _add_to_aggregate(ledger_usage, call_usage)
        target = priced_usage if call.get("cost_status") == "priced" else unpriced_usage
        _add_to_aggregate(target, call_usage)
        if call.get("cost_status") == "priced":
            try:
                amount += Decimal(str((call.get("cost") or {}).get("amount", "0")))
            except Exception:
                unpriced_calls.append(call)

    unledgered_usage = {
        key: max(0, _safe_int(usage.get(key)) - ledger_usage[key])
        for key in TOKEN_KEYS
    }
    has_unledgered = any(unledgered_usage.values())
    has_priced = bool(priced_calls)
    has_unpriced = bool(unpriced_calls) or has_unledgered
    has_usage = any(_safe_int(usage.get(key)) for key in TOKEN_KEYS)
    if not has_usage:
        status = "no_usage"
    elif has_priced and not has_unpriced:
        status = "priced"
    elif has_priced:
        status = "partially_priced"
    else:
        status = "unpriced"

    for key in TOKEN_KEYS:
        unpriced_usage[key] += unledgered_usage[key]
    return {
        "cost_status": status,
        "currency": currency,
        "amount": decimal_string(amount),
        "priced_call_count": len(priced_calls),
        "unpriced_call_count": len(unpriced_calls) + unledgered_usage["call_count"],
        "priced_usage": priced_usage,
        "unpriced_usage": unpriced_usage,
    }


def _attach_group_costs(
    groups: dict[str, dict[str, Any]],
    calls: list[dict[str, Any]],
    *,
    group_key: str,
    currency: str,
) -> None:
    for value, usage in groups.items():
        group_calls = [call for call in calls if str(call.get(group_key, "")) == value]
        usage["cost"] = _cost_summary(usage, group_calls, currency)


def _iter_node_states(node_states: dict, parent_id: str = ""):
    """Yield top-level and subprocess child states with stable qualified IDs."""
    for raw_node_id, raw_state in node_states.items():
        node_id = f"{parent_id}/{raw_node_id}" if parent_id else str(raw_node_id)
        if isinstance(raw_state, dict):
            state = raw_state
        else:
            state = vars(raw_state)
        yield node_id, str(raw_node_id), state
        child_states = state.get("child_states", {})
        if isinstance(child_states, dict):
            yield from _iter_node_states(child_states, node_id)


def _iter_rejection_history(state: dict[str, Any]):
    """Yield loop snapshots followed by the active iteration without duplicates."""
    histories = []
    for iteration in state.get("iteration_history", []):
        if isinstance(iteration, dict):
            histories.append(iteration.get("rejection_history", []))
    histories.append(state.get("rejection_history", []))
    seen: set[str] = set()
    for history in histories:
        if not isinstance(history, list):
            continue
        for event in history:
            if not isinstance(event, dict):
                continue
            rejection_id = str(event.get("rejection_id") or "")
            identity = rejection_id or json.dumps(
                event, ensure_ascii=False, sort_keys=True, default=str
            )
            if identity in seen:
                continue
            seen.add(identity)
            yield event


def aggregate_token_usage(
    node_states: dict,
    node_agent_map: dict[str, str],
    *,
    pricing_config: dict[str, Any] | None = None,
    pricing_path: str | None = None,
) -> dict:
    """Aggregate node usage, call ledgers and configured costs.

    Legacy tasks without a call ledger retain their token totals and are marked
    ``unpriced`` because their invocation timestamps and price versions cannot
    be reconstructed safely.
    """
    nodes_detail: list[dict] = []
    by_model: dict[str, dict[str, Any]] = {}
    by_agent_type: dict[str, dict[str, Any]] = {}
    total: dict[str, Any] = dict(ZERO_USAGE)
    raw_calls: list[dict[str, Any]] = []
    raw_rejections: list[dict[str, Any]] = []
    seen_call_ids: set[str] = set()

    for node_id, raw_node_id, state in _iter_node_states(node_states):
        token_usage = state.get("token_usage")
        token_usage = token_usage if isinstance(token_usage, dict) else {}
        raw_node_calls = state.get("token_usage_calls", [])
        raw_node_calls = raw_node_calls if isinstance(raw_node_calls, list) else []
        raw_rejections.extend(dict(event) for event in _iter_rejection_history(state))
        if not token_usage and not raw_node_calls:
            continue

        agent_type = node_agent_map.get(
            node_id,
            node_agent_map.get(raw_node_id, "unknown"),
        )
        status = str(state.get("status", "unknown"))
        session_id = str(state.get("session_id", ""))
        node_calls: list[dict[str, Any]] = []
        for item in raw_node_calls:
            if not isinstance(item, dict):
                continue
            normalized = _normalize_call(
                item,
                node_id=node_id,
                agent_type=agent_type,
                status=status,
                session_id=session_id,
            )
            call_id = str(normalized.get("call_id", ""))
            if call_id and call_id in seen_call_ids:
                continue
            if call_id:
                seen_call_ids.add(call_id)
            node_calls.append(normalized)
        if agent_type == "unknown" and node_calls:
            recorded_agent_type = str(node_calls[0].get("agent_type", "unknown"))
            if recorded_agent_type:
                agent_type = recorded_agent_type
                for call in node_calls:
                    call["agent_type"] = agent_type
        raw_calls.extend(node_calls)

        node_usage = dict(ZERO_USAGE)
        model_ids: list[str] = []
        for model_id, model_data in token_usage.items():
            if not isinstance(model_data, dict):
                continue
            model_id = str(model_id)
            model_ids.append(model_id)
            if model_id not in by_model:
                by_model[model_id] = dict(ZERO_USAGE)
            _add_to_aggregate(by_model[model_id], model_data)
            _add_to_aggregate(node_usage, model_data)

            if agent_type not in by_agent_type:
                by_agent_type[agent_type] = dict(ZERO_USAGE)
            _add_to_aggregate(by_agent_type[agent_type], model_data)
            _add_to_aggregate(total, model_data)

        nodes_detail.append({
            "node_id": node_id,
            "agent_type": agent_type,
            "status": status,
            "session_id": session_id,
            "model_ids": model_ids,
            "token_usage": deepcopy(token_usage),
            "usage": node_usage,
            "calls": node_calls,
        })

    priced = price_usage_calls(
        raw_calls,
        pricing_config=pricing_config,
        pricing_path=pricing_path,
    )
    priced_calls = priced["calls"]
    currency = priced["pricing_snapshot"]["currency"]
    calls_by_id = {
        str(call.get("call_id", "")): call
        for call in priced_calls
        if call.get("call_id")
    }

    for node in nodes_detail:
        node_calls = []
        for raw_call in node["calls"]:
            call_id = str(raw_call.get("call_id", ""))
            if call_id and call_id in calls_by_id:
                node_calls.append(calls_by_id[call_id])
            else:
                matching = next(
                    (
                        call for call in priced_calls
                        if call.get("node_id") == node["node_id"]
                        and call.get("session_id") == raw_call.get("session_id")
                        and call.get("call_index") == raw_call.get("call_index")
                    ),
                    raw_call,
                )
                node_calls.append(matching)
        node["calls"] = node_calls
        node["cost"] = _cost_summary(node["usage"], node_calls, currency)

    _attach_group_costs(
        by_model, priced_calls, group_key="model_id", currency=currency,
    )
    _attach_group_costs(
        by_agent_type, priced_calls, group_key="agent_type", currency=currency,
    )
    total_cost = _cost_summary(total, priced_calls, currency)
    total["cost"] = total_cost
    rejections = []
    for event in raw_rejections:
        retry_call_ids = {
            str(call_id) for call_id in event.get("retry_call_ids", []) if call_id
        }
        event_calls = [
            call for call in priced_calls
            if str(call.get("call_id", "")) in retry_call_ids
        ]
        retry_usage = dict(ZERO_USAGE)
        for call in event_calls:
            _add_to_aggregate(retry_usage, _usage_from_call(call))
        rejections.append({
            **event,
            "retry_usage": retry_usage,
            "retry_cost": _cost_summary(retry_usage, event_calls, currency),
        })

    return {
        "calls": priced_calls,
        "rejections": rejections,
        "nodes": nodes_detail,
        "by_model": by_model,
        "by_agent_type": by_agent_type,
        "total": total,
        "cost": total_cost,
        "cost_status": total_cost["cost_status"],
        "currency": currency,
        "pricing_snapshot": priced["pricing_snapshot"],
    }
