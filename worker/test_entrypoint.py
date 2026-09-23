import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worker.entrypoint import execute_job


class WorkerContractTests(unittest.TestCase):
    def test_blocked_research_never_becomes_publishable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prices = root / "prices.csv"
            targets = root / "targets.csv"
            engine = root / "engine"
            prices.write_text("date,ticker,adjusted_close\n2025-01-02,SPY,100\n")
            targets.write_text("signal_at,execution_date,security_id,target_weight,provenance_id\n")
            engine.write_text("placeholder")
            engine.chmod(0o755)

            def fake_engine(**kwargs):
                output = kwargs["output_dir"]
                output.mkdir()
                (output / "summary.csv").write_text("metric,portfolio,benchmark\n")
                return output

            with patch("worker.entrypoint.run_engine", side_effect=fake_engine):
                result = execute_job(
                    "blocked-job",
                    "backtest",
                    {
                        "prices": str(prices),
                        "targets": str(targets),
                        "engine": str(engine),
                        "research_outcome": "blocked",
                        "available_at": "2026-09-21T15:00:00Z",
                    },
                    root / "outputs",
                )

            self.assertEqual(result["execution_state"], "succeeded")
            self.assertEqual(result["research_outcome"], "blocked")
            self.assertFalse(result["publishable"])
            saved = json.loads((root / "outputs/blocked-job/job_result.json").read_text())
            self.assertFalse(saved["publishable"])

    def test_existing_job_directory_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prices = root / "prices.csv"
            targets = root / "targets.csv"
            engine = root / "engine"
            for path in (prices, targets):
                path.write_text("date,ticker,adjusted_close\n")
            engine.write_text("placeholder")
            engine.chmod(0o755)
            output_root = root / "outputs"
            output_root.mkdir()
            (output_root / "same-job").mkdir()
            with self.assertRaisesRegex(ValueError, "will not be overwritten"):
                execute_job(
                    "same-job",
                    "backtest",
                    {
                        "prices": str(prices),
                        "targets": str(targets),
                        "engine": str(engine),
                        "available_at": "2026-09-21T15:00:00Z",
                    },
                    output_root,
                )


if __name__ == "__main__":
    unittest.main()
