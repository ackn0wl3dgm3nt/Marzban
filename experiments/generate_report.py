#!/usr/bin/env python3
"""
Generates a markdown benchmark report from JSON results.

Usage:
    python generate_report.py benchmark.json --title "My Benchmark" --output report.md
    python generate_report.py benchmark.json  # prints to stdout
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt(val, suffix="ms"):
    if isinstance(val, float):
        return f"{val:.1f}{suffix}"
    return str(val)


def generate_report(data: dict, title: str, branch: str = "", commit: str = "") -> str:
    lines = []

    # Header
    lines.append(f"# {title}")
    lines.append("")

    if branch:
        lines.append(f"**Branch:** `{branch}`")
    lines.append(f"**Date:** {data.get('timestamp', 'N/A')}")
    if commit:
        lines.append(f"**Commit:** `{commit}`")
    lines.append("")

    # Environment
    server = data.get("server", {})
    system = server.get("system", {})
    nodes = server.get("nodes", [])
    inbounds = server.get("inbounds", {})
    params = data.get("params", {})

    lines.append("## Environment")
    lines.append("")
    lines.append("| Parameter | Value |")
    lines.append("|-----------|-------|")
    lines.append(f"| CPU | {system.get('cpu_cores', '?')} cores |")
    lines.append(f"| RAM | {system.get('mem_total', 0) / 1024**3:.1f} GB |")
    lines.append(f"| Existing users | {server.get('total_users', '?'):,} |")
    lines.append(f"| Nodes | {len(nodes)} ({', '.join(n.get('name', '?') for n in nodes) if nodes else 'master only'}) |")

    inbound_str = ", ".join(
        f"{proto} ({len(tags)})"
        for proto, tags in inbounds.items()
    ) if inbounds else "N/A"
    lines.append(f"| Inbounds | {inbound_str} |")
    lines.append("")

    # Test parameters
    lines.append("## Test Parameters")
    lines.append("")
    lines.append("| Parameter | Value |")
    lines.append("|-----------|-------|")
    lines.append(f"| Test users | {params.get('users', '?')} |")
    lines.append(f"| Concurrency | {params.get('concurrent', '?')} |")
    lines.append("")

    # Results table
    results = data.get("results", [])
    lines.append("## Results")
    lines.append("")
    lines.append("### Throughput & Latency")
    lines.append("")
    lines.append("| Operation | Requests | Success | RPS | Avg (ms) | P50 (ms) | P95 (ms) | P99 (ms) |")
    lines.append("|-----------|----------|---------|-----|----------|----------|----------|----------|")

    for r in results:
        total = r.get("total", 0)
        success = r.get("success", 0)
        pct = f"{success/total*100:.0f}%" if total > 0 else "—"
        rps = r.get("rps", 0)
        avg = r.get("avg_ms", 0)
        p50 = r.get("p50_ms", 0)
        p95 = r.get("p95_ms", 0)
        p99 = r.get("p99_ms", 0)

        if total == 0:
            continue

        lines.append(
            f"| {r['operation']} | {total} | {success} ({pct}) | "
            f"**{rps:.1f}** | {avg:.1f} | {p50:.1f} | {p95:.1f} | {p99:.1f} |"
        )

    lines.append("")

    # Profiler breakdown
    profiler = data.get("profiler", {})

    for phase_key, phase_label in [("disable", "DISABLE (active -> disabled)"),
                                    ("enable", "ENABLE (disabled -> active)")]:
        prof = profiler.get(phase_key, {})
        if not prof:
            continue

        lines.append(f"### Profiler: {phase_label}")
        lines.append("")
        lines.append("| Component | Calls | Avg (ms) | P95 (ms) | P99 (ms) | % of Route |")
        lines.append("|-----------|-------|----------|----------|----------|------------|")

        # Find route total for percentage calculation
        route_avg = 0
        for name, d in prof.items():
            if name.startswith("route."):
                route_avg = max(route_avg, d.get("avg_ms", 0))

        sorted_prof = sorted(prof.items(), key=lambda x: x[1].get("total_ms", 0), reverse=True)
        for name, d in sorted_prof:
            calls = d.get("calls", 0)
            avg = d.get("avg_ms", 0)
            p95 = d.get("p95_ms", 0)
            p99 = d.get("p99_ms", 0)
            pct = f"{avg/route_avg*100:.1f}%" if route_avg > 0 else "—"

            bold = "**" if name.startswith("route.") or name.startswith("crud.") else ""
            lines.append(
                f"| {bold}{name}{bold} | {calls} | {avg:.1f} | {p95:.1f} | {p99:.1f} | {pct} |"
            )

        lines.append("")

    # Analysis
    lines.append("## Analysis")
    lines.append("")

    for phase_key in ["disable", "enable"]:
        prof = profiler.get(phase_key, {})
        if not prof:
            continue

        route_time = crud_time = xray_time = grpc_time = 0
        for name, d in prof.items():
            avg = d.get("avg_ms", 0)
            if name.startswith("route."):
                route_time = max(route_time, avg)
            elif name.startswith("crud."):
                crud_time = max(crud_time, avg)
            elif name.startswith("xray.execute") or name.startswith("xray.do_"):
                xray_time = max(xray_time, avg)
            elif name.startswith("xray.grpc"):
                grpc_time = max(grpc_time, avg)

        if route_time > 0:
            lines.append(f"**{phase_key.upper()} breakdown:**")
            lines.append("```")
            lines.append(f"Route total:   {route_time:.0f} ms")
            lines.append(f"  DB/CRUD:     {crud_time:.0f} ms ({crud_time/route_time*100:.0f}%)")
            lines.append(f"  XrayManager: {xray_time:.0f} ms ({xray_time/route_time*100:.0f}%)")
            lines.append(f"  gRPC:        {grpc_time:.0f} ms ({grpc_time/route_time*100:.0f}%)")
            lines.append("```")
            lines.append("")

    # Key metrics summary
    switch_results = [r for r in results if "SWITCH" in r.get("operation", "")]
    if switch_results:
        lines.append("### Key Metrics")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")

        rps_values = [r["rps"] for r in switch_results if r.get("rps")]
        avg_values = [r["avg_ms"] for r in switch_results if r.get("avg_ms")]
        if rps_values:
            lines.append(f"| SWITCH RPS | {min(rps_values):.0f} - {max(rps_values):.0f} |")
        if avg_values:
            lines.append(f"| Route avg latency | {min(avg_values):.0f} - {max(avg_values):.0f} ms |")

        # DB metrics from profiler
        for phase_key in ["disable", "enable"]:
            prof = profiler.get(phase_key, {})
            for name, d in prof.items():
                if name.startswith("crud."):
                    lines.append(f"| DB/CRUD avg ({phase_key}) | {d.get('avg_ms', 0):.0f} ms |")
                    break

        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate markdown benchmark report from JSON")
    parser.add_argument("json_file", help="Path to benchmark JSON file")
    parser.add_argument("--title", default="Benchmark Report", help="Report title")
    parser.add_argument("--branch", default="", help="Git branch name")
    parser.add_argument("--commit", default="", help="Git commit hash")
    parser.add_argument("--output", "-o", default="", help="Output .md file (default: stdout)")

    args = parser.parse_args()

    data = load_json(args.json_file)
    report = generate_report(data, args.title, args.branch, args.commit)

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"Report saved to: {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
