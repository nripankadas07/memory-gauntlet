"""Scenario validation, execution, and deterministic scoring."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import SCENARIO_VERSION, SCHEMA_VERSION, __version__
from .adapters import MemoryAdapter, make_adapter


CATEGORIES = {"recall", "correction", "deletion", "ttl", "role", "privacy"}
OPS = {"write", "correct", "delete", "advance", "query"}
EXPECTATION_KEYS = ("visible_ids", "hidden_ids", "contains", "excludes")


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_string_list(value: Any, label: str) -> List[str]:
    if not isinstance(value, list):
        return ["%s must be a list of non-empty strings" % label]
    if any(not _non_empty_string(item) for item in value):
        return ["%s must contain only non-empty strings" % label]
    if len(set(value)) != len(value):
        return ["%s must not contain duplicate assertions" % label]
    return []


def _tokens(value: str) -> set:
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def _query_reaches(query: str, text: str) -> bool:
    return bool(_tokens(query) & _tokens(text))


def _contains_fragment(values: Sequence[str], text: str) -> bool:
    lowered = text.lower()
    return any(value.lower() in lowered for value in values)


def _visible_binding(expected: Mapping[str, Any], memory_id: str, text: str) -> bool:
    return memory_id in expected.get("visible_ids", []) or _contains_fragment(
        expected.get("contains", []), text
    )


def _hidden_binding(expected: Mapping[str, Any], memory_id: str, text: str) -> bool:
    return memory_id in expected.get("hidden_ids", []) or _contains_fragment(
        expected.get("excludes", []), text
    )


def _authorized(record: Mapping[str, Any], actor: str, roles: Mapping[str, str]) -> bool:
    readers = set(record["readers"])
    return bool(
        actor == record["owner"]
        or actor in readers
        or roles[actor] in readers
        or "*" in readers
    )


def _validate_scenario_semantics(
    steps: Sequence[Mapping[str, Any]], roles: Mapping[str, str]
) -> List[str]:
    """Bind every scored query to state established by preceding operations."""
    errors: List[str] = []
    now = 0
    records: Dict[str, Dict[str, Any]] = {}
    for index, step in enumerate(steps):
        prefix = "steps[%d]" % index
        op = step["op"]
        if op == "write":
            memory_id = step["memory_id"]
            if memory_id in records:
                errors.append("%s writes duplicate memory_id %s" % (prefix, memory_id))
                continue
            ttl = step.get("ttl")
            records[memory_id] = {
                "owner": step["actor"],
                "text": step["text"],
                "readers": list(step.get("readers", [])),
                "stale_texts": [],
                "expires_at": None if ttl is None else now + ttl,
                "deleted": False,
                "deleted_while_live": False,
            }
            continue
        if op == "correct":
            memory_id = step["memory_id"]
            record = records.get(memory_id)
            if record is None or record["deleted"]:
                errors.append("%s corrects an unknown or deleted memory_id %s" % (prefix, memory_id))
                continue
            if record["owner"] != step["actor"]:
                errors.append("%s correction actor must own memory_id %s" % (prefix, memory_id))
                continue
            record["stale_texts"].append(record["text"])
            record["text"] = step["text"]
            if "ttl" in step:
                record["expires_at"] = now + step["ttl"]
            continue
        if op == "delete":
            memory_id = step["memory_id"]
            record = records.get(memory_id)
            if record is None or record["deleted"]:
                errors.append("%s deletes an unknown or already deleted memory_id %s" % (prefix, memory_id))
                continue
            if record["owner"] != step["actor"]:
                errors.append("%s deletion actor must own memory_id %s" % (prefix, memory_id))
                continue
            record["deleted_while_live"] = (
                record["expires_at"] is None or record["expires_at"] > now
            )
            record["deleted"] = True
            continue
        if op == "advance":
            now += step["seconds"]
            continue
        if op != "query":
            continue

        actor = step["actor"]
        query = step["query"]
        expected = step["expect"]
        category = step["category"]
        if category == "recall":
            candidates = [
                (memory_id, record)
                for memory_id, record in records.items()
                if not record["deleted"]
                and (record["expires_at"] is None or record["expires_at"] > now)
                and _authorized(record, actor, roles)
                and _query_reaches(query, record["text"])
                and _visible_binding(expected, memory_id, record["text"])
            ]
            if not candidates:
                errors.append(
                    "%s recall assertions must bind to a visible, query-relevant existing memory"
                    % prefix
                )
            continue
        if category == "correction":
            candidates = []
            for memory_id, record in records.items():
                if (
                    record["deleted"]
                    or not record["stale_texts"]
                    or (record["expires_at"] is not None and record["expires_at"] <= now)
                    or not _authorized(record, actor, roles)
                    or not _query_reaches(query, record["text"])
                    or not _visible_binding(expected, memory_id, record["text"])
                ):
                    continue
                stale_bound = any(
                    _query_reaches(query, stale_text)
                    and _contains_fragment(expected.get("excludes", []), stale_text)
                    for stale_text in record["stale_texts"]
                )
                if stale_bound:
                    candidates.append((memory_id, record))
            if not candidates:
                errors.append(
                    "%s correction assertions must bind current and excluded stale text to the same corrected, query-relevant memory"
                    % prefix
                )
            continue
        if category == "deletion":
            candidates = [
                (memory_id, record)
                for memory_id, record in records.items()
                if record["deleted"]
                and record["deleted_while_live"]
                and _query_reaches(query, record["text"])
                and _hidden_binding(expected, memory_id, record["text"])
            ]
            if not candidates:
                errors.append(
                    "%s deletion assertions must bind to a query-relevant memory deleted while live"
                    % prefix
                )
            continue
        if category == "ttl":
            candidates = [
                (memory_id, record)
                for memory_id, record in records.items()
                if not record["deleted"]
                and record["expires_at"] is not None
                and record["expires_at"] <= now
                and _query_reaches(query, record["text"])
                and _hidden_binding(expected, memory_id, record["text"])
            ]
            if not candidates:
                errors.append(
                    "%s ttl assertions must bind to a query-relevant memory whose TTL has expired"
                    % prefix
                )
            continue
        if category in {"privacy", "role"}:
            candidates = [
                (memory_id, record)
                for memory_id, record in records.items()
                if not record["deleted"]
                and (record["expires_at"] is None or record["expires_at"] > now)
                and not _authorized(record, actor, roles)
                and _query_reaches(query, record["text"])
                and _hidden_binding(expected, memory_id, record["text"])
            ]
            if not candidates:
                errors.append(
                    "%s %s assertions must bind to an existing, query-relevant memory inaccessible to the querying actor"
                    % (prefix, category)
                )
    return errors


def validate_scenario(value: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if value.get("schema_version") != SCENARIO_VERSION:
        errors.append("schema_version must be %s" % SCENARIO_VERSION)
    if not _non_empty_string(value.get("id")):
        errors.append("id must be a non-empty string")
    principals = value.get("principals")
    if not isinstance(principals, list) or not principals:
        errors.append("principals must be a non-empty list")
        principals = []
    principal_ids = set()
    principal_roles = set()
    for principal in principals:
        if (
            not isinstance(principal, dict)
            or not _non_empty_string(principal.get("id"))
            or not _non_empty_string(principal.get("role"))
        ):
            errors.append("each principal requires non-empty string id and role")
        else:
            if principal["id"] in principal_ids:
                errors.append("principal ids must be unique: %s" % principal["id"])
            principal_ids.add(principal["id"])
            principal_roles.add(principal["role"])
    steps = value.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("steps must be a non-empty list")
        return errors
    query_count = 0
    for index, step in enumerate(steps):
        prefix = "steps[%d]" % index
        if not isinstance(step, dict) or step.get("op") not in OPS:
            errors.append("%s has unsupported op" % prefix)
            continue
        actor = step.get("actor")
        if step["op"] != "advance" and actor not in principal_ids:
            errors.append("%s references unknown actor" % prefix)
        if step["op"] in {"write", "correct", "delete"} and not _non_empty_string(step.get("memory_id")):
            errors.append("%s requires non-empty string memory_id" % prefix)
        if step["op"] == "write" and not _non_empty_string(step.get("text")):
            errors.append("%s requires non-empty string text" % prefix)
        if step["op"] == "correct" and not _non_empty_string(step.get("text")):
            errors.append("%s requires non-empty corrected string text" % prefix)
        if "ttl" in step and (
            type(step.get("ttl")) is not int or step.get("ttl", -1) < 0
        ):
            errors.append("%s ttl must be a non-negative integer" % prefix)
        if step["op"] == "write" and "readers" in step:
            errors.extend(_validate_string_list(step.get("readers"), "%s readers" % prefix))
            readers = step.get("readers")
            if isinstance(readers, list):
                known = principal_ids | principal_roles | {"*"}
                unknown = sorted(
                    item for item in readers if isinstance(item, str) and item not in known
                )
                if unknown:
                    errors.append("%s readers reference unknown principals or roles: %s" % (prefix, ", ".join(unknown)))
        if step["op"] == "advance" and (
            type(step.get("seconds")) is not int or step.get("seconds", -1) < 0
        ):
            errors.append("%s requires non-negative integer seconds" % prefix)
        if step["op"] == "query":
            query_count += 1
            category = step.get("category")
            if category not in CATEGORIES:
                errors.append("%s requires a supported category" % prefix)
            if not _non_empty_string(step.get("query")):
                errors.append("%s query must be a non-empty string" % prefix)
            if "limit" in step and (
                type(step.get("limit")) is not int or step.get("limit", 0) <= 0
            ):
                errors.append("%s limit must be a positive integer" % prefix)
            expected = step.get("expect")
            if not isinstance(expected, dict):
                errors.append("%s requires expect object" % prefix)
                continue
            unknown_keys = sorted(set(expected) - set(EXPECTATION_KEYS))
            if unknown_keys:
                errors.append("%s expect contains unsupported keys: %s" % (prefix, ", ".join(unknown_keys)))
            for key in EXPECTATION_KEYS:
                if key in expected:
                    errors.extend(
                        _validate_string_list(expected[key], "%s expect.%s" % (prefix, key))
                    )
            visible = sum(
                len(expected.get(key, []))
                for key in ("visible_ids", "contains")
                if isinstance(expected.get(key, []), list)
            )
            hidden = sum(
                len(expected.get(key, []))
                for key in ("hidden_ids", "excludes")
                if isinstance(expected.get(key, []), list)
            )
            if visible + hidden == 0:
                errors.append("%s expect must contain at least one assertion" % prefix)
            if category == "recall" and visible == 0:
                errors.append("%s recall query requires a visible assertion" % prefix)
            if category == "correction" and (visible == 0 or hidden == 0):
                errors.append("%s correction query requires visible and hidden assertions" % prefix)
            if category in {"deletion", "ttl", "role", "privacy"} and hidden == 0:
                errors.append("%s %s query requires a hidden assertion" % (prefix, category))
    if query_count == 0:
        errors.append("steps must include at least one query")
    if not errors:
        roles = {principal["id"]: principal["role"] for principal in principals}
        errors.extend(_validate_scenario_semantics(steps, roles))
    return errors


def load_scenario(path_value: str) -> Dict[str, Any]:
    path = Path(path_value)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("cannot load scenario %s: %s" % (path, exc)) from exc
    if not isinstance(value, dict):
        raise ValueError("scenario root must be an object: %s" % path)
    errors = validate_scenario(value)
    if errors:
        raise ValueError("invalid scenario %s: %s" % (path, "; ".join(errors)))
    return value


def _rate(hits: int, total: int) -> Optional[float]:
    return round(hits / total, 4) if total else None


def run_scenarios(scenarios: Sequence[Mapping[str, Any]], adapter: MemoryAdapter) -> Dict[str, Any]:
    counters: Dict[str, int] = {
        "recall_hits": 0,
        "recall_total": 0,
        "stale_failures": 0,
        "stale_total": 0,
        "deletion_failures": 0,
        "deletion_total": 0,
        "ttl_failures": 0,
        "ttl_total": 0,
        "leakage_failures": 0,
        "leakage_total": 0,
    }
    observations: List[Dict[str, Any]] = []
    scenario_summaries: List[Dict[str, Any]] = []
    adapter_name = adapter.name
    aggregate_stats: Dict[str, int] = {key: 0 for key in adapter.stats}
    original_available = True
    try:
        if not scenarios:
            raise ValueError("at least one scenario is required")
        for scenario in scenarios:
            validation_errors = validate_scenario(scenario)
            if validation_errors:
                raise ValueError(
                    "invalid scenario %s: %s"
                    % (scenario.get("id", "<unknown>"), "; ".join(validation_errors))
                )
        for scenario_index, scenario in enumerate(scenarios):
            scenario_adapter = adapter if scenario_index == 0 else adapter.fresh()
            if scenario_index == 0:
                original_available = False
            roles = {item["id"]: item["role"] for item in scenario["principals"]}
            start_observations = len(observations)
            try:
                for sequence, step in enumerate(scenario["steps"], 1):
                    op = step["op"]
                    if op == "write":
                        scenario_adapter.write(
                            step["memory_id"],
                            step["actor"],
                            step["text"],
                            step.get("readers", []),
                            step.get("ttl"),
                        )
                    elif op == "correct":
                        scenario_adapter.correct(step["memory_id"], step["actor"], step["text"], step.get("ttl"))
                    elif op == "delete":
                        scenario_adapter.delete(step["memory_id"], step["actor"])
                    elif op == "advance":
                        scenario_adapter.advance(step["seconds"])
                    elif op == "query":
                        results = scenario_adapter.query(step["actor"], roles[step["actor"]], step.get("query", ""), step.get("limit", 5))
                        ids = [item.memory_id for item in results]
                        joined_text = "\n".join(item.text for item in results).lower()
                        expected = step["expect"]
                        visible_ids = list(expected.get("visible_ids", []))
                        hidden_ids = list(expected.get("hidden_ids", []))
                        contains = [value.lower() for value in expected.get("contains", [])]
                        excludes = [value.lower() for value in expected.get("excludes", [])]
                        category = step["category"]
                        visible_hits = sum(1 for value in visible_ids if value in ids) + sum(
                            1 for value in contains if value in joined_text
                        )
                        visible_total = len(visible_ids) + len(contains)
                        hidden_failures = sum(1 for value in hidden_ids if value in ids) + sum(
                            1 for value in excludes if value in joined_text
                        )
                        hidden_total = len(hidden_ids) + len(excludes)
                        if category in {"recall", "correction"}:
                            counters["recall_hits"] += visible_hits
                            counters["recall_total"] += visible_total
                        if category == "correction":
                            counters["stale_failures"] += hidden_failures
                            counters["stale_total"] += hidden_total
                        elif category == "deletion":
                            counters["deletion_failures"] += hidden_failures
                            counters["deletion_total"] += hidden_total
                        elif category == "ttl":
                            counters["ttl_failures"] += hidden_failures
                            counters["ttl_total"] += hidden_total
                        elif category in {"role", "privacy"}:
                            counters["leakage_failures"] += hidden_failures
                            counters["leakage_total"] += hidden_total
                        observations.append(
                            {
                                "scenario_id": scenario["id"],
                                "sequence": sequence,
                                "category": category,
                                "actor": step["actor"],
                                "query": step.get("query", ""),
                                "expected": expected,
                                "results": [item.as_dict() for item in results],
                                "visible_hits": visible_hits,
                                "visible_total": visible_total,
                                "hidden_failures": hidden_failures,
                                "hidden_total": hidden_total,
                                "passed": visible_hits == visible_total and hidden_failures == 0,
                            }
                        )
            finally:
                for key, value in scenario_adapter.stats.items():
                    aggregate_stats[key] = aggregate_stats.get(key, 0) + value
                scenario_adapter.close()
            scenario_observations = observations[start_observations:]
            scenario_summaries.append(
                {
                    "id": scenario["id"],
                    "queries": len(scenario_observations),
                    "passed": sum(1 for item in scenario_observations if item["passed"]),
                    "failed": sum(1 for item in scenario_observations if not item["passed"]),
                }
            )
    finally:
        if original_available:
            adapter.close()

    recall = _rate(counters["recall_hits"], counters["recall_total"])
    stale_rate = _rate(counters["stale_failures"], counters["stale_total"])
    deletion_failure_rate = _rate(counters["deletion_failures"], counters["deletion_total"])
    ttl_failure_rate = _rate(counters["ttl_failures"], counters["ttl_total"])
    leakage_rate = _rate(counters["leakage_failures"], counters["leakage_total"])
    deletion_compliance = None if deletion_failure_rate is None else round(1.0 - deletion_failure_rate, 4)
    ttl_compliance = None if ttl_failure_rate is None else round(1.0 - ttl_failure_rate, 4)
    isolation = None if leakage_rate is None else round(1.0 - leakage_rate, 4)
    dimension_scores = {
        "recall": recall,
        "freshness": None if stale_rate is None else round(1.0 - stale_rate, 4),
        "deletion": deletion_compliance,
        "ttl": ttl_compliance,
        "isolation": isolation,
    }
    exercised = sorted(name for name, score in dimension_scores.items() if score is not None)
    composite = round(
        sum(score for score in dimension_scores.values() if score is not None) / len(exercised),
        4,
    )
    query_count = aggregate_stats["queries"]
    cost_per_query = round(aggregate_stats["records_scanned"] / query_count, 4) if query_count else 0.0
    return {
        "schema_version": SCHEMA_VERSION,
        "runner_version": __version__,
        "adapter": adapter_name,
        "scenario_ids": [scenario["id"] for scenario in scenarios],
        "scorecard": {
            "recall": recall,
            "stale_rate": stale_rate,
            "deletion_compliance": deletion_compliance,
            "ttl_compliance": ttl_compliance,
            "privacy_leakage_rate": leakage_rate,
            "role_isolation": isolation,
            "composite": composite,
            "exercised_dimensions": exercised,
            "coverage": round(len(exercised) / len(dimension_scores), 4),
            "cost": dict(aggregate_stats, records_per_query=cost_per_query),
        },
        "counters": counters,
        "scenarios": scenario_summaries,
        "observations": observations,
        "limits": [
            "The benchmark measures declared synthetic expectations, not human memory quality.",
            "Lexical retrieval is a deterministic baseline, not a semantic-memory claim.",
            "Cost counts records inspected and operations; it is not financial cost or latency.",
            "Passing scenarios does not prove regulatory privacy compliance.",
        ],
    }


def run_paths(paths: Sequence[str], adapter_name: str) -> Dict[str, Any]:
    if not paths:
        raise ValueError("at least one scenario path is required")
    scenarios = [load_scenario(path) for path in paths]
    return run_scenarios(scenarios, make_adapter(adapter_name))
