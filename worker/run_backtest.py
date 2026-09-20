"""Invoke the C++ backtest executable and return its CSV output directory.

The worker owns database reads and result persistence. The C++ process only
receives validated CSV snapshots and writes summary/equity/trade CSV files.
"""

import argparse
import subprocess
from pathlib import Path


def run_engine(
    engine: Path,
    prices_path: Path,
    universe_path: Path,
    output_dir: Path,
    benchmark: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Path:
    command = [
        str(engine),
        "--prices", str(prices_path),
        "--universe", str(universe_path),
        "--output-dir", str(output_dir),
        "--benchmark", benchmark,
    ]
    if start_date:
        command.extend(["--start", start_date])
    if end_date:
        command.extend(["--end", end_date])

    completed = subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=True,
    )
    if not (output_dir / "summary.csv").is_file():
        raise RuntimeError(
            "backtest engine completed without summary.csv: "
            f"{completed.stdout.strip()}"
        )
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--start")
    parser.add_argument("--end")
    args = parser.parse_args()

    output_dir = run_engine(
        args.engine,
        args.prices,
        args.universe,
        args.output_dir,
        args.benchmark,
        args.start,
        args.end,
    )
    print(f"Backtest outputs: {output_dir}")


if __name__ == "__main__":
    main()