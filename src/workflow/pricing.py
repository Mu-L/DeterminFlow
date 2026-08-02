"""Versioned, time-aware pricing for persisted LLM usage calls.

Pricing is deliberately configuration-only. Invalid configuration and
incomplete provider usage are fail-closed: the call remains visible, but it is
reported as unpriced instead of being assigned a guessed or zero cost.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.config import LLM_PRICING_CONFIG_FILE


DEFAULT_PRICING_CONFIG: dict[str, Any] = {
    "version": "unconfigured",
    "currency": "USD",
    "timezone": "UTC",
    "unit_tokens": 1_000_000,
    "rules": [],
}

_RATE_ALIASES = (
    ("prompt", "input"),
    ("cached_prompt", "cached_input"),
    ("completion", "output"),
    ("reasoning",),
)
_ALLOWED_RATE_KEYS = {name for aliases in _RATE_ALIASES for name in aliases}
_DAY_ALIASES = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def _decimal_string(value: Decimal) -> str:
    if not value:
        return "0"
    return format(value.normalize(), "f")


def _as_decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not result.is_finite() or result < 0:
        return None
    return result


def _as_token_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0 or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def _zoneinfo(name: Any) -> ZoneInfo | None:
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _parse_datetime(value: Any, default_tz: ZoneInfo) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed


def _parse_clock(value: Any) -> time | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = time.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is None else None


def _validate_pricing_config(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """Return a normalized config and stable validation error codes."""
    errors: list[str] = []
    if not isinstance(raw, dict):
        return deepcopy(DEFAULT_PRICING_CONFIG), ["root:not_object"]

    config = deepcopy(DEFAULT_PRICING_CONFIG)
    config.update(deepcopy(raw))

    for field in ("version", "currency", "timezone"):
        value = config.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field}:invalid_string")

    config_tz = _zoneinfo(config.get("timezone"))
    if config_tz is None:
        errors.append("timezone:invalid_timezone")
        config_tz = ZoneInfo("UTC")

    unit_tokens = _as_token_int(config.get("unit_tokens"))
    if unit_tokens is None or unit_tokens <= 0:
        errors.append("unit_tokens:invalid_integer")
        unit_tokens = 0
    config["unit_tokens"] = unit_tokens if unit_tokens > 0 else 1_000_000

    rules = config.get("rules")
    if not isinstance(rules, list):
        errors.append("rules:not_list")
        config["rules"] = []
        return config, errors

    seen_rule_ids: set[str] = set()
    for rule_index, rule in enumerate(rules):
        prefix = f"rules[{rule_index}]"
        if not isinstance(rule, dict):
            errors.append(f"{prefix}:not_object")
            continue

        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id.strip():
            errors.append(f"{prefix}.id:invalid_string")
        elif rule_id in seen_rule_ids:
            errors.append(f"{prefix}.id:duplicate")
        else:
            seen_rule_ids.add(rule_id)

        for field in ("provider", "model"):
            value = rule.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{prefix}.{field}:invalid_string")

        rule_tz = config_tz
        if "timezone" in rule and rule.get("timezone") is not None:
            rule_tz = _zoneinfo(rule.get("timezone"))
            if rule_tz is None:
                errors.append(f"{prefix}.timezone:invalid_timezone")
                rule_tz = config_tz

        effective_values: dict[str, datetime | None] = {}
        for field in ("effective_from", "effective_to"):
            value = rule.get(field)
            if value is None:
                effective_values[field] = None
                continue
            parsed = _parse_datetime(value, rule_tz)
            effective_values[field] = parsed
            if parsed is None:
                errors.append(f"{prefix}.{field}:invalid_datetime")
        effective_from = effective_values.get("effective_from")
        effective_to = effective_values.get("effective_to")
        if effective_from and effective_to:
            if effective_to.astimezone(timezone.utc) <= effective_from.astimezone(
                timezone.utc
            ):
                errors.append(f"{prefix}.effective_window:invalid_range")

        rates = rule.get("rates")
        if not isinstance(rates, dict):
            errors.append(f"{prefix}.rates:not_object")
        else:
            unknown_keys = sorted(set(rates) - _ALLOWED_RATE_KEYS, key=str)
            for key in unknown_keys:
                errors.append(f"{prefix}.rates.{key}:unknown_rate")
            for aliases in _RATE_ALIASES:
                present = [name for name in aliases if name in rates]
                if len(present) > 1:
                    errors.append(
                        f"{prefix}.rates.{aliases[0]}:duplicate_alias"
                    )
                for name in present:
                    if _as_decimal(rates[name]) is None:
                        errors.append(f"{prefix}.rates.{name}:invalid_decimal")
            if not any(name in rates for name in ("prompt", "input")):
                errors.append(f"{prefix}.rates.prompt:missing")
            if not any(name in rates for name in ("completion", "output")):
                errors.append(f"{prefix}.rates.completion:missing")

        time_bands = rule.get("time_bands", [])
        if not isinstance(time_bands, list):
            errors.append(f"{prefix}.time_bands:not_list")
            continue
        for band_index, band in enumerate(time_bands):
            band_prefix = f"{prefix}.time_bands[{band_index}]"
            if not isinstance(band, dict):
                errors.append(f"{band_prefix}:not_object")
                continue
            start = _parse_clock(band.get("start"))
            end = _parse_clock(band.get("end"))
            if start is None:
                errors.append(f"{band_prefix}.start:invalid_time")
            if end is None:
                errors.append(f"{band_prefix}.end:invalid_time")
            if start is not None and end is not None and start == end:
                errors.append(f"{band_prefix}.window:invalid_range")
            multiplier = _as_decimal(band.get("multiplier"))
            if multiplier is None or multiplier <= 0:
                errors.append(f"{band_prefix}.multiplier:invalid_decimal")
            days = band.get("days")
            if days is not None:
                if not isinstance(days, list) or not days:
                    errors.append(f"{band_prefix}.days:invalid_list")
                else:
                    for day in days:
                        valid_int = (
                            isinstance(day, int)
                            and not isinstance(day, bool)
                            and 0 <= day <= 6
                        )
                        valid_name = (
                            isinstance(day, str)
                            and day.strip().lower() in _DAY_ALIASES
                        )
                        if not valid_int and not valid_name:
                            errors.append(f"{band_prefix}.days:invalid_day")
                            break

    return config, errors


def _pricing_snapshot(
    config: dict[str, Any],
    *,
    source: str,
    status: str,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    snapshot = {
        "source": source,
        "config_status": status,
        "valuation_mode": "current_config",
        "version": str(config.get("version", "unconfigured")),
        "currency": str(config.get("currency", "USD")),
        "timezone": str(config.get("timezone", "UTC")),
        "unit_tokens": config.get("unit_tokens", 1_000_000),
        "matched_rules": [],
    }
    if errors:
        snapshot["validation_errors"] = errors
    return snapshot


def load_pricing_config(
    path: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load a pricing table and return it with a public-safe source status."""
    config_path = Path(path) if path is not None else LLM_PRICING_CONFIG_FILE
    source = config_path.name or "pricing_config"
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        config = deepcopy(DEFAULT_PRICING_CONFIG)
        return config, _pricing_snapshot(
            config, source=source, status="missing",
        )
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        config = deepcopy(DEFAULT_PRICING_CONFIG)
        return config, _pricing_snapshot(
            config,
            source=source,
            status="config_invalid",
            errors=["root:unreadable_or_invalid_json"],
        )

    config, errors = _validate_pricing_config(raw)
    status = "config_invalid" if errors else "loaded"
    return config, _pricing_snapshot(
        config, source=source, status=status, errors=errors,
    )


def _matches_identity(rule: dict[str, Any], call: dict[str, Any]) -> bool:
    provider = str(call.get("provider", ""))
    model = str(call.get("model", ""))
    rule_provider = str(rule.get("provider", "*"))
    rule_model = str(rule.get("model", "*"))
    return rule_provider in {"*", provider} and rule_model in {"*", model}


def _matches_effective_window(
    rule: dict[str, Any],
    occurred_at: datetime,
    config_tz: ZoneInfo,
) -> bool:
    effective_from = _parse_datetime(rule.get("effective_from"), config_tz)
    effective_to = _parse_datetime(rule.get("effective_to"), config_tz)
    occurred_utc = occurred_at.astimezone(timezone.utc)
    if effective_from and occurred_utc < effective_from.astimezone(timezone.utc):
        return False
    if effective_to and occurred_utc >= effective_to.astimezone(timezone.utc):
        return False
    return True


def _effective_sort_key(rule: dict[str, Any], config_tz: ZoneInfo) -> tuple:
    parsed = _parse_datetime(rule.get("effective_from"), config_tz)
    epoch = parsed.timestamp() if parsed else float("-inf")
    specificity = int(rule.get("provider", "*") != "*") + int(
        rule.get("model", "*") != "*"
    )
    return epoch, specificity, str(rule.get("id", ""))


def _select_rule(
    config: dict[str, Any],
    call: dict[str, Any],
    occurred_at: datetime,
    config_tz: ZoneInfo,
) -> dict[str, Any] | None:
    matches = [
        rule
        for rule in config.get("rules", [])
        if isinstance(rule, dict)
        and _matches_identity(rule, call)
        and _matches_effective_window(rule, occurred_at, config_tz)
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: _effective_sort_key(item, config_tz))


def _matches_weekday(band: dict[str, Any], local_dt: datetime) -> bool:
    days = band.get("days")
    if days is None:
        return True
    normalized = {
        day if isinstance(day, int) else _DAY_ALIASES[day.strip().lower()]
        for day in days
    }
    return local_dt.weekday() in normalized


def _select_time_band(
    rule: dict[str, Any],
    local_dt: datetime,
) -> tuple[dict[str, Any] | None, Decimal]:
    for band in rule.get("time_bands", []):
        if not _matches_weekday(band, local_dt):
            continue
        start = _parse_clock(band.get("start"))
        end = _parse_clock(band.get("end"))
        if start is None or end is None:
            continue
        current = local_dt.timetz().replace(tzinfo=None)
        if start < end:
            in_band = start <= current < end
        else:
            in_band = current >= start or current < end
        if not in_band:
            continue
        multiplier = _as_decimal(band.get("multiplier"))
        if multiplier is not None:
            return band, multiplier
    return None, Decimal("1")


def _rate(rates: dict[str, Any], *names: str) -> Decimal | None:
    for name in names:
        if name in rates:
            return _as_decimal(rates[name])
    return None


def _unpriced_call(
    call: dict[str, Any],
    *,
    currency: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    priced_call = deepcopy(call)
    priced_call.pop("pricing_snapshot", None)
    cost = {"amount": "0", "reason": reason}
    if details:
        cost.update(details)
    priced_call.update({
        "cost_status": "unpriced",
        "currency": currency,
        "cost": cost,
    })
    return priced_call


def _legacy_usage_can_be_inferred(call: dict[str, Any]) -> bool:
    """Allow non-zero pre-marker ledgers only when their breakdown is provable."""
    prompt = _as_token_int(call.get("prompt_tokens"))
    completion = _as_token_int(call.get("completion_tokens"))
    total = _as_token_int(call.get("total_tokens"))
    return (
        prompt is not None
        and completion is not None
        and total is not None
        and total > 0
        and total == prompt + completion
    )


def _price_call(
    call: dict[str, Any],
    config: dict[str, Any],
    base_snapshot: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    currency = base_snapshot["currency"]
    usage_status = call.get("usage_status")
    legacy_usage_inferred = (
        usage_status is None and _legacy_usage_can_be_inferred(call)
    )
    if usage_status != "complete" and not legacy_usage_inferred:
        missing = call.get("missing_usage_fields")
        if not isinstance(missing, list):
            missing = [
                field
                for field in ("prompt_tokens", "completion_tokens")
                if field not in call
            ]
        usage_errors = call.get("usage_errors")
        if not isinstance(usage_errors, list):
            usage_errors = []
        return _unpriced_call(
            call,
            currency=currency,
            reason="incomplete_usage",
            details={
                "missing_fields": sorted(set(map(str, missing))),
                "usage_errors": sorted(set(map(str, usage_errors))),
            },
        ), None

    config_tz = _zoneinfo(base_snapshot["timezone"])
    if config_tz is None:
        return _unpriced_call(
            call, currency=currency, reason="config_invalid",
        ), None
    occurred_at = _parse_datetime(call.get("timestamp"), config_tz)
    if occurred_at is None:
        return _unpriced_call(
            call, currency=currency, reason="missing_or_invalid_timestamp",
        ), None

    rule = _select_rule(config, call, occurred_at, config_tz)
    if rule is None:
        return _unpriced_call(
            call, currency=currency, reason="no_matching_price_rule",
        ), None

    token_values: dict[str, int] = {}
    invalid_token_fields: list[str] = []
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = _as_token_int(call.get(field))
        if value is None:
            invalid_token_fields.append(field)
        else:
            token_values[field] = value
    for field in ("cached_tokens", "reasoning_tokens"):
        value = _as_token_int(call.get(field, 0))
        if value is None:
            invalid_token_fields.append(field)
        else:
            token_values[field] = value
    if invalid_token_fields:
        return _unpriced_call(
            call,
            currency=currency,
            reason="invalid_usage",
            details={"invalid_fields": sorted(invalid_token_fields)},
        ), None

    prompt_tokens = token_values["prompt_tokens"]
    completion_tokens = token_values["completion_tokens"]
    total_tokens = token_values["total_tokens"]
    cached_tokens = token_values["cached_tokens"]
    reasoning_tokens = token_values["reasoning_tokens"]
    inconsistent_fields: list[str] = []
    if total_tokens != prompt_tokens + completion_tokens:
        inconsistent_fields.append("total_tokens")
    if cached_tokens > prompt_tokens:
        inconsistent_fields.append("cached_tokens")
    if reasoning_tokens > completion_tokens:
        inconsistent_fields.append("reasoning_tokens")
    if inconsistent_fields:
        return _unpriced_call(
            call,
            currency=currency,
            reason="invalid_usage_breakdown",
            details={"invalid_fields": inconsistent_fields},
        ), None

    rule_tz_name = str(rule.get("timezone") or base_snapshot["timezone"])
    rule_tz = _zoneinfo(rule_tz_name)
    if rule_tz is None:
        return _unpriced_call(
            call, currency=currency, reason="config_invalid",
        ), None
    local_dt = occurred_at.astimezone(rule_tz)
    band, multiplier = _select_time_band(rule, local_dt)
    rates = rule["rates"]
    prompt_rate = _rate(rates, "prompt", "input")
    cached_rate = _rate(rates, "cached_prompt", "cached_input")
    completion_rate = _rate(rates, "completion", "output")
    reasoning_rate = _rate(rates, "reasoning")
    rule_snapshot = {
        "rule_id": str(rule.get("id", "")),
        "provider": str(rule.get("provider", "*")),
        "model": str(rule.get("model", "*")),
        "effective_from": rule.get("effective_from"),
        "effective_to": rule.get("effective_to"),
        "timezone": rule_tz_name,
        "rates": deepcopy(rates),
        "time_band": deepcopy(band) if band else None,
        "multiplier": _decimal_string(multiplier),
    }

    missing_rates: list[str] = []
    if prompt_tokens - cached_tokens and prompt_rate is None:
        missing_rates.append("prompt")
    if cached_tokens and cached_rate is None:
        missing_rates.append("cached_prompt")
    if completion_tokens and completion_rate is None:
        missing_rates.append("completion")
    if missing_rates:
        priced_call = _unpriced_call(
            call,
            currency=currency,
            reason="missing_rates",
            details={"missing_rates": sorted(set(missing_rates))},
        )
        priced_call["pricing_snapshot"] = rule_snapshot
        return priced_call, rule_snapshot

    unit = Decimal(config["unit_tokens"])
    uncached_prompt = Decimal(prompt_tokens - cached_tokens)
    cached = Decimal(cached_tokens)
    reasoning = Decimal(reasoning_tokens)
    if reasoning_rate is None:
        regular_completion = Decimal(completion_tokens)
        reasoning = Decimal(0)
    else:
        regular_completion = Decimal(completion_tokens - reasoning_tokens)

    prompt_amount = uncached_prompt * (prompt_rate or Decimal(0)) / unit * multiplier
    cached_amount = cached * (cached_rate or Decimal(0)) / unit * multiplier
    completion_amount = (
        regular_completion * (completion_rate or Decimal(0)) / unit * multiplier
    )
    reasoning_amount = reasoning * (reasoning_rate or Decimal(0)) / unit * multiplier
    amount = prompt_amount + cached_amount + completion_amount + reasoning_amount

    priced_call = deepcopy(call)
    priced_call.update({
        "cost_status": "priced",
        "currency": currency,
        "cost": {
            "amount": _decimal_string(amount),
            "prompt": _decimal_string(prompt_amount),
            "cached_prompt": _decimal_string(cached_amount),
            "completion": _decimal_string(completion_amount),
            "reasoning": _decimal_string(reasoning_amount),
        },
        "pricing_snapshot": rule_snapshot,
    })
    if legacy_usage_inferred:
        priced_call["usage_pricing_status"] = "legacy_inferred_complete"
    return priced_call, rule_snapshot


def price_usage_calls(
    calls: list[dict[str, Any]],
    *,
    pricing_config: dict[str, Any] | None = None,
    pricing_path: str | Path | None = None,
) -> dict[str, Any]:
    """Price call-ledger rows without mutating the persisted ledger."""
    if pricing_config is None:
        config, snapshot = load_pricing_config(pricing_path)
    else:
        config, errors = _validate_pricing_config(pricing_config)
        snapshot = _pricing_snapshot(
            config,
            source="inline",
            status="config_invalid" if errors else "loaded",
            errors=errors,
        )

    priced_calls: list[dict[str, Any]] = []
    matched_rules: dict[str, dict[str, Any]] = {}
    for raw_call in calls:
        call = raw_call if isinstance(raw_call, dict) else {}
        if snapshot["config_status"] != "loaded":
            reason = (
                "config_invalid"
                if snapshot["config_status"] == "config_invalid"
                else "pricing_config_missing"
            )
            priced = _unpriced_call(
                call, currency=snapshot["currency"], reason=reason,
            )
            rule_snapshot = None
        else:
            priced, rule_snapshot = _price_call(call, config, snapshot)
        priced_calls.append(priced)
        if rule_snapshot:
            rule_key = json.dumps(rule_snapshot, ensure_ascii=False, sort_keys=True)
            matched_rules[rule_key] = rule_snapshot
    snapshot["matched_rules"] = list(matched_rules.values())
    return {"calls": priced_calls, "pricing_snapshot": snapshot}


def sum_cost_amount(calls: list[dict[str, Any]]) -> Decimal:
    """Return the exact configured cost of priced calls."""
    amount = Decimal(0)
    for call in calls:
        if call.get("cost_status") != "priced":
            continue
        parsed = _as_decimal((call.get("cost") or {}).get("amount"))
        if parsed is not None:
            amount += parsed
    return amount


def decimal_string(value: Decimal) -> str:
    """Public JSON formatting helper used by usage aggregation."""
    return _decimal_string(value)
