"""Scenario validation, execution, and deterministic scoring."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import SCENARIO_VERSION, SCHEMA_VERSION, __version__
from .adapters import MAX_LOGICAL_TIME, MemoryAdapter, make_adapter


CATEGORIES = {"recall", "correction", "deletion", "ttl", "role", "privacy"}
OPS = {"write", "correct", "delete", "advance", "query"}
EXPECTATION_KEYS = ("visible_ids", "hidden_ids", "contains", "excludes")
CATEGORY_EXPECTATION_KEYS = {
    "recall": {"visible_ids", "contains"},
    "correction": {"visible_ids", "contains", "excludes"},
    "deletion": {"hidden_ids", "excludes"},
    "ttl": {"hidden_ids", "excludes"},
    "role": {"hidden_ids", "excludes"},
    "privacy": {"hidden_ids", "excludes"},
}


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


def _all_visible_assertions_bind(
    expected: Mapping[str, Any], candidates: Sequence[tuple]
) -> bool:
    return all(
        any(memory_id == expected_id for memory_id, _ in candidates)
        for expected_id in expected.get("visible_ids", [])
    ) and all(
        any(fragment.lower() in record["text"].lower() for _, record in candidates)
        for fragment in expected.get("contains", [])
    )


def _all_hidden_assertions_bind(
    expected: Mapping[str, Any], candidates: Sequence[tuple], reachable: Sequence[tuple]
) -> bool:
    return all(
        any(memory_id == expected_id for memory_id, _ in candidates)
        and any(memory_id == expected_id for memory_id, _, _ in reachable)
        for expected_id in expected.get("hidden_ids", [])
    ) and all(
        any(
            fragment.lower() in record["text"].lower()
            and any(
                reachable_id == memory_id
                and fragment.lower() in reachable_text.lower()
                for reachable_id, reachable_text, _ in reachable
            )
            for memory_id, record in candidates
        )
        for fragment in expected.get("excludes", [])
    )


def _ungoverned_results(
    records: Mapping[str, Mapping[str, Any]], query: str, limit: int
) -> List[tuple]:
    query_tokens = _tokens(query)
    matches = []
    for memory_id, record in records.items():
        versions = list(record["stale_texts"]) + [record["text"]]
        for version, text in enumerate(versions, 1):
            score = len(query_tokens & _tokens(text))
            if score > 0 or not query_tokens:
                matches.append((memory_id, text, version, score))
    ranked = sorted(matches, key=lambda item: (-item[3], item[0], item[2]))[:limit]
    return [(memory_id, text, version) for memory_id, text, version, _ in ranked]


def _correction_assertions_bind_same_record(
    expected: Mapping[str, Any],
    candidates: Sequence[tuple],
    query: str,
    reachable: Sequence[tuple],
) -> bool:
    for memory_id, record in candidates:
        if any(
            expected_id != memory_id
            for expected_id in expected.get("visible_ids", [])
        ):
            continue
        if any(
            fragment.lower() not in record["text"].lower()
            for fragment in expected.get("contains", [])
        ):
            continue
        stale_bound = True
        for fragment in expected.get("excludes", []):
            matching_stale = [
                stale_text
                for stale_text in record["stale_texts"]
                if fragment.lower() in stale_text.lower()
                and _query_reaches(query, stale_text)
            ]
            if not matching_stale or not any(
                reachable_id == memory_id and reachable_text in matching_stale
                for reachable_id, reachable_text, _ in reachable
            ):
                stale_bound = False
                break
        if stale_bound:
            return True
    return False


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
            if ttl is not None and ttl > MAX_LOGICAL_TIME - now:
                errors.append("%s ttl exceeds the bounded logical-time range" % prefix)
                continue
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
            ttl = step.get("ttl")
            if ttl is not None and ttl > MAX_LOGICAL_TIME - now:
                errors.append("%s ttl exceeds the bounded logical-time range" % prefix)
                continue
            record["stale_texts"].append(record["text"])
            record["text"] = step["text"]
            if ttl is not None:
                record["expires_at"] = now + ttl
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
            if step["seconds"] > MAX_LOGICAL_TIME - now:
                errors.append("%s exceeds the bounded logical-time range" % prefix)
            else:
                now += step["seconds"]
            continue
        if op != "query":
            continue

        actor = step["actor"]
        query = step["query"]
        expected = step["expect"]
        category = step["category"]
        reachable = _ungoverned_results(
            records, query, step.get("limit", 5)
        )
        if category == "recall":
            candidates = [
                (memory_id, record)
                for memory_id, record in records.items()
                if not record["deleted"]
                and (record["expires_at"] is None or record["expires_at"] > now)
                and _authorized(record, actor, roles)
                and _query_reaches(query, record["text"])
            ]
            if not candidates or not _all_visible_assertions_bind(expected, candidates):
                errors.append(
                    "%s recall assertions must each bind to visible, query-relevant existing memory"
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
                ):
                    continue
                candidates.append((memory_id, record))
            if (
                not candidates
                or not _correction_assertions_bind_same_record(
                    expected, candidates, query, reachable
                )
            ):
                errors.append(
                    (
                        "%s correction assertions must each bind current and excluded "
                        "stale text to corrected, query-relevant memory; use excludes "
                        "for stale text"
                    )
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
            ]
            if not candidates or not _all_hidden_assertions_bind(
                expected, candidates, reachable
            ):
                errors.append(
                    "%s deletion assertions must each bind to query-relevant memory deleted while live"
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
            ]
            if not candidates or not _all_hidden_assertions_bind(
                expected, candidates, reachable
            ):
                errors.append(
                    "%s ttl assertions must each bind to query-relevant memory whose TTL has expired"
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
            ]
            if not candidates or not _all_hidden_assertions_bind(
                expected, candidates, reachable
            ):
                errors.append(
                    (
                        "%s %s assertions must each bind to existing, query-relevant "
                        "memory inaccessible to the querying actor"
                    )
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
            type(step.get("ttl")) is not int
            or step.get("ttl", -1) < 0
            or step.get("ttl", MAX_LOGICAL_TIME + 1) > MAX_LOGICAL_TIME
        ):
            errors.append(
                "%s ttl must be an integer from 0 through %d"
                % (prefix, MAX_LOGICAL_TIME)
            )
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
            type(step.get("seconds")) is not int
            or step.get("seconds", -1) < 0
            or step.get("seconds", MAX_LOGICAL_TIME + 1) > MAX_LOGICAL_TIME
        ):
            errors.append(
                "%s seconds must be an integer from 0 through %d"
                % (prefix, MAX_LOGICAL_TIME)
            )
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
            if category in CATEGORY_EXPECTATION_KEYS:
                category_unknown = sorted(
                    set(expected) - CATEGORY_EXPECTATION_KEYS[category]
                )
                if category_unknown:
                    errors.append(
                        "%s %s query does not allow expectation keys: %s"
                        % (prefix, category, ", ".join(category_unknown))
                    )
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
            correction_stale = (
                len(expected.get("excludes", []))
                if isinstance(expected.get("excludes", []), list)
                else 0
            )
            if category == "correction" and (
                visible == 0 or correction_stale == 0
            ):
                errors.append(
                    "%s correction query requires visible current and excludes stale assertions"
                    % prefix
                )
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


def require_unique_scenario_ids(scenarios: Sequence[Mapping[str, Any]]) -> None:
    seen = set()
    duplicates = set()
    for scenario in scenarios:
        identifier = scenario.get("id")
        if identifier in seen:
            duplicates.add(identifier)
        seen.add(identifier)
    if duplicates:
        raise ValueError(
            "scenario ids must be unique within a run: %s" % ", ".join(sorted(duplicates))
        )


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
        require_unique_scenario_ids(scenarios)
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
