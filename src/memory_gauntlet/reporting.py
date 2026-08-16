"""Stable JSON, Markdown, and single-file HTML reports."""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


def stable_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _metric(value: Any, digits: int = 4) -> str:
    return "n/a" if value is None else ("%." + str(digits) + "f") % value


def _markdown_literal(value: Any) -> str:
    """Encode untrusted values so they cannot alter Markdown structure."""
    safe = []
    for character in str(value):
        if character.isalnum() or character == " ":
            safe.append(character)
        else:
            safe.append("&#%d;" % ord(character))
    return "".join(safe)


def markdown_report(runs: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# Memory Gauntlet report",
        "",
        "| Adapter | Recall | Stale rate | Deletion | TTL | Leakage | Composite | Coverage | Records/query |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run in runs:
        score = run["scorecard"]
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %s | %s | %s |"
            % (
                _markdown_literal(run["adapter"]),
                _metric(score["recall"]),
                _metric(score["stale_rate"]),
                _metric(score["deletion_compliance"]),
                _metric(score["ttl_compliance"]),
                _metric(score["privacy_leakage_rate"]),
                _metric(score["composite"]),
                _metric(score["coverage"]),
                _metric(score["cost"]["records_per_query"]),
            )
        )
    lines.extend(["", "## Query evidence", ""])
    for run in runs:
        lines.append("### %s" % _markdown_literal(run["adapter"]))
        lines.append("")
        for item in run["observations"]:
            mark = "PASS" if item["passed"] else "FAIL"
            lines.append(
                "- **%s** `%s` step %d (%s): returned `%s`"
                % (
                    mark,
                    _markdown_literal(item["scenario_id"]),
                    item["sequence"],
                    _markdown_literal(item["category"]),
                    ", ".join(
                        _markdown_literal(result["memory_id"])
                        for result in item["results"]
                    )
                    or "none",
                )
            )
        lines.append("")
    lines.extend(["## Limits", ""])
    limits = runs[0]["limits"] if runs else []
    lines.extend("- %s" % _markdown_literal(value) for value in limits)
    lines.append("")
    return "\n".join(lines)


def html_report(runs: Sequence[Mapping[str, Any]]) -> str:
    rows = []
    for run in runs:
        score = run["scorecard"]
        rows.append(
            "<tr><td><strong>%s</strong></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class='composite'>%s</td><td>%s</td></tr>"
            % (
                html.escape(str(run["adapter"])),
                _metric(score["recall"], 3),
                _metric(score["stale_rate"], 3),
                _metric(score["deletion_compliance"], 3),
                _metric(score["ttl_compliance"], 3),
                _metric(score["privacy_leakage_rate"], 3),
                _metric(score["composite"], 3),
                _metric(score["coverage"], 3),
            )
        )
    evidence = []
    for run in runs:
        failed = [item for item in run["observations"] if not item["passed"]]
        evidence.append(
            "<section><h2>%s</h2><p>%d of %d checks failed.</p><ul>%s</ul></section>"
            % (
                html.escape(str(run["adapter"])),
                len(failed),
                len(run["observations"]),
                "".join(
                    "<li><code>%s/%s</code>: %s</li>"
                    % (
                        html.escape(str(item["scenario_id"])),
                        item["sequence"],
                        html.escape(", ".join(result["text"] for result in item["results"]) or "no result"),
                    )
                    for item in failed
                )
                or "<li>No failed checks.</li>",
            )
        )
    embedded = html.escape(stable_json(list(runs)))
    return """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Memory Gauntlet report</title><style>
body{font:15px system-ui,sans-serif;max-width:1050px;margin:40px auto;padding:0 20px;color:#182230;background:#f7f9fc}h1{margin-bottom:4px}.lede{color:#475467}table{width:100%%;border-collapse:collapse;background:white;border:1px solid #e4e7ec;border-radius:12px}th,td{padding:12px;text-align:right;border-bottom:1px solid #e4e7ec}th:first-child,td:first-child{text-align:left}.composite{font-weight:800;color:#067647}section{background:white;border:1px solid #e4e7ec;padding:16px 20px;margin-top:18px;border-radius:12px}code{white-space:pre-wrap}pre{background:#101828;color:#f2f4f7;padding:16px;border-radius:8px;overflow:auto}details{margin-top:24px}
</style><h1>Memory Gauntlet</h1><p class="lede">Governance behavior under correction, deletion, TTL, role, and privacy pressure.</p>
<table><thead><tr><th>Adapter</th><th>Recall</th><th>Stale</th><th>Deletion</th><th>TTL</th><th>Leakage</th><th>Composite</th><th>Coverage</th></tr></thead><tbody>%s</tbody></table>%s
<details><summary>Canonical results</summary><pre>%s</pre></details></html>""" % ("".join(rows), "".join(evidence), embedded)


def write_bundle(runs: Sequence[Mapping[str, Any]], output_value: str) -> Path:
    output = Path(output_value)
    output.mkdir(parents=True, exist_ok=True)
    comparison = {
        "schema_version": "memory-gauntlet-comparison/v1",
        "runs": [
            {"adapter": run["adapter"], "scorecard": run["scorecard"], "scenario_ids": run["scenario_ids"]}
            for run in runs
        ],
    }
    files = {
        "results.json": stable_json(list(runs)),
        "scorecard.json": stable_json(comparison),
        "report.md": markdown_report(runs),
        "report.html": html_report(runs),
    }
    checksums = []
    for name, content in sorted(files.items()):
        (output / name).write_text(content, encoding="utf-8")
        checksums.append("%s  %s" % (hashlib.sha256(content.encode("utf-8")).hexdigest(), name))
    (output / "checksums.sha256").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    return output
