"""Command line for Memory Gauntlet."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import __version__
from .reporting import write_bundle
from .runner import load_scenario, run_paths


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memory-gauntlet", description="Evaluate governance properties of agent memory.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate scenario JSON")
    validate.add_argument("scenarios", nargs="+")

    run = sub.add_parser("run", help="run scenarios against one adapter")
    run.add_argument("scenarios", nargs="+")
    run.add_argument("--adapter", choices=["governed", "leaky"], default="governed")
    run.add_argument("--out", default="artifacts/run")

    compare = sub.add_parser("compare", help="compare governed and leaky baselines")
    compare.add_argument("scenarios", nargs="+")
    compare.add_argument("--out", default="artifacts/compare")

    demo = sub.add_parser("demo", help="run bundled offline scenarios")
    demo.add_argument("--out", default="artifacts/demo")
    return parser


def _demo_paths() -> List[str]:
    root = Path(__file__).resolve().parent / "data"
    paths = [str(path) for path in sorted(root.glob("*.json"))]
    if not paths:
        raise ValueError("bundled demo contains no scenarios")
    return paths


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            for path in args.scenarios:
                load_scenario(path)
            print("valid: %d scenario file(s)" % len(args.scenarios))
            return 0
        if args.command == "run":
            result = run_paths(args.scenarios, args.adapter)
            output = write_bundle([result], args.out)
            print("%s composite %.4f; report %s" % (result["adapter"], result["scorecard"]["composite"], output / "report.html"))
            return 0
        if args.command in {"compare", "demo"}:
            paths = args.scenarios if args.command == "compare" else _demo_paths()
            governed = run_paths(paths, "governed")
            leaky = run_paths(paths, "leaky")
            output = write_bundle([governed, leaky], args.out)
            print("governed %.4f vs leaky %.4f" % (governed["scorecard"]["composite"], leaky["scorecard"]["composite"]))
            print("report: %s" % (output / "report.html"))
            return 0
    except (OSError, ValueError) as exc:
        print("memory-gauntlet: error: %s" % exc, file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
