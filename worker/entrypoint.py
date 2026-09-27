"""Run one validated research job without owning queues or persistence.

The backend/queue layer supplies a unique job id, a server-owned output root,
and validated JSON parameters. This process only executes the supported
backtest operation and returns a durable result contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from worker.run_backtest import run_engine


SUPPORTED_OPERATIONS = {"backtest"}
RESEARCH_OUTCOMES = {"collecting", "pending", "blocked", "completed"}
JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TICKER = re.compile(r"^[A-Za-z0-9._-]{1,16}$")
METHODOLOGY_VERSION = "worker-backtest-v1"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENGINE = REPO_ROOT / "backtest" / "build" / "portfolio_backtest"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("available_at must include a timezone")
    return parsed.isoformat().replace("+00:00", "Z")


def _required_file(parameters: dict[str, Any], *names: str) -> Path:
    for name in names:
        value = parameters.get(name)
        if value is not None:
            path = Path(str(value)).expanduser()
            if not path.is_file():
                raise ValueError(f"{name} must point to an existing file: {path}")
            return path.resolve()
    raise ValueError(f"missing required parameter: {names[0]}")


def _finite_number(parameters: dict[str, Any], name: str, default: float) -> float:
    value = parameters.get(name, default)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def validate_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the backend-to-worker parameter contract."""
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be a JSON object")
    allowed = {
        "prices",
        "prices_path",
        "targets",
        "targets_path",
        "engine",
        "engine_path",
        "benchmark",
        "start_date",
        "end_date",
        "cost_bps",
        "initial_capital",
        "research_outcome",
        "available_at",
        "methodology_version",
    }
    unknown = sorted(set(parameters) - allowed)
    if unknown:
        raise ValueError("unsupported parameters: " + ", ".join(unknown))

    prices = _required_file(parameters, "prices", "prices_path")
    targets = _required_file(parameters, "targets", "targets_path")
    engine = Path(
        str(parameters.get("engine", parameters.get("engine_path", DEFAULT_ENGINE)))
    ).expanduser()
    if not engine.is_file() or not engine.stat().st_mode & 0o111:
        raise ValueError(f"engine must be an executable file: {engine}")

    benchmark = str(parameters.get("benchmark", "SPY")).strip().upper()
    if not TICKER.fullmatch(benchmark):
        raise ValueError("benchmark must be a simple security identifier")

    start_date = parameters.get("start_date")
    end_date = parameters.get("end_date")
    if start_date is not None:
        date.fromisoformat(str(start_date))
    if end_date is not None:
        date.fromisoformat(str(end_date))
    if start_date and end_date and str(start_date) > str(end_date):
        raise ValueError("start_date cannot be after end_date")

    cost_bps = _finite_number(parameters, "cost_bps", 10.0)
    initial_capital = _finite_number(parameters, "initial_capital", 100_000.0)
    if cost_bps < 0:
        raise ValueError("cost_bps cannot be negative")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")

    research_outcome = str(parameters.get("research_outcome", "pending")).strip().lower()
    if research_outcome not in RESEARCH_OUTCOMES:
        raise ValueError("research_outcome must be one of: " + ", ".join(sorted(RESEARCH_OUTCOMES)))

    methodology_version = str(
        parameters.get("methodology_version", METHODOLOGY_VERSION)
    ).strip()
    if not methodology_version:
        raise ValueError("methodology_version cannot be blank")

    available_at = _json_timestamp(parameters.get("available_at"))
    if available_at is None:
        raise ValueError("available_at is required for point-in-time execution")

    return {
        "prices": str(prices),
        "targets": str(targets),
        "engine": str(engine.resolve()),
        "benchmark": benchmark,
        "start_date": str(start_date) if start_date is not None else None,
        "end_date": str(end_date) if end_date is not None else None,
        "cost_bps": cost_bps,
        "initial_capital": initial_capital,
        "research_outcome": research_outcome,
        "available_at": available_at,
        "methodology_version": methodology_version,
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _base_result(job_id: str, operation: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "operation": operation,
        "execution_state": "running",
        "research_outcome": parameters.get("research_outcome", "pending"),
        "publishable": False,
        "validation_messages": [],
        "output_manifest": None,
        "artifact_locations": {},
        "input_hashes": {},
        "available_at": parameters.get("available_at"),
        "methodology_version": parameters.get("methodology_version", METHODOLOGY_VERSION),
    }


def _finalize(result: dict[str, Any], job_dir: Path, parameters: dict[str, Any]) -> None:
    artifacts = {
        str(path.relative_to(job_dir)): str(path)
        for path in sorted(job_dir.rglob("*"))
        if path.is_file() and path.name not in {"job_result.json", "manifest.json"}
    }
    result["artifact_locations"] = artifacts
    manifest = {
        "job_id": result["job_id"],
        "operation": result["operation"],
        "execution_state": result["execution_state"],
        "research_outcome": result["research_outcome"],
        "publishable": result["publishable"],
        "parameters": parameters,
        "input_hashes": result["input_hashes"],
        "available_at": result["available_at"],
        "methodology_version": result["methodology_version"],
        "validation_messages": result["validation_messages"],
        "artifacts": artifacts,
    }
    manifest_path = job_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    result["output_manifest"] = str(manifest_path)
    _write_json(job_dir / "job_result.json", result)


def execute_job(
    job_id: str,
    operation: str,
    parameters: dict[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    """Execute one job in an isolated, non-overwriting directory."""
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("job_id must contain only letters, numbers, '.', '_' or '-'")
    operation = operation.strip().lower()
    if operation not in SUPPORTED_OPERATIONS:
        raise ValueError(f"unsupported operation: {operation}")
    if not output_root.is_absolute():
        raise ValueError("output directory must be an absolute server-controlled path")

    normalized = validate_parameters(parameters)
    output_root.mkdir(parents=True, exist_ok=True)
    job_dir = output_root / job_id
    if job_dir.exists():
        raise ValueError(f"job output already exists and will not be overwritten: {job_dir}")
    job_dir.mkdir()
    result = _base_result(job_id, operation, normalized)
    result["input_hashes"] = {
        "prices": _sha256(Path(normalized["prices"])),
        "targets": _sha256(Path(normalized["targets"])),
    }
    _write_json(job_dir / "job_result.json", result)

    try:
        run_engine(
            engine=Path(normalized["engine"]),
            prices_path=Path(normalized["prices"]),
            targets_path=Path(normalized["targets"]),
            output_dir=job_dir / "backtest",
            benchmark=normalized["benchmark"],
            start_date=normalized["start_date"],
            end_date=normalized["end_date"],
            cost_bps=normalized["cost_bps"],
            initial_capital=normalized["initial_capital"],
        )
        result["execution_state"] = "succeeded"
        if result["research_outcome"] == "completed":
            result["publishable"] = True
        else:
            result["validation_messages"].append(
                {
                    "code": "RESEARCH_NOT_COMPLETED",
                    "severity": "warning",
                    "message": "engine succeeded, but research outcome is not completed; performance is not publishable",
                }
            )
    except Exception as exc:  # noqa: BLE001 - convert process failures to the job contract
        result["execution_state"] = "failed"
        result["research_outcome"] = "blocked"
        result["validation_messages"].append(
            {"code": "JOB_FAILED", "severity": "error", "message": str(exc)}
        )
    finally:
        _finalize(result, job_dir, normalized)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--parameters-json", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        parameters = json.loads(args.parameters_json)
        result = execute_job(
            args.job_id,
            args.operation,
            parameters,
            Path(args.output_dir).expanduser().resolve(),
        )
    except Exception as exc:  # noqa: BLE001 - CLI must return structured validation failures
        result = {
            "job_id": args.job_id,
            "operation": args.operation,
            "execution_state": "failed",
            "research_outcome": "blocked",
            "publishable": False,
            "validation_messages": [
                {"code": "INVALID_JOB", "severity": "error", "message": str(exc)}
            ],
            "output_manifest": None,
            "artifact_locations": {},
            "input_hashes": {},
            "available_at": None,
            "methodology_version": METHODOLOGY_VERSION,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["execution_state"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
