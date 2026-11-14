import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ai_model import forecast_worker, predictor, simulation


def _load_sim_client():
    client_path = Path(__file__).resolve().parent.parent / "simulation_scripts" / ".sim_client.py"
    spec = importlib.util.spec_from_file_location("simulation_scripts_client", client_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sim_client = _load_sim_client()


class ClearSimulatedDataTests(unittest.TestCase):
    def test_clear_simulated_data_waits_for_lock_commits_and_unlocks(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        sleeps = []
        reports = []
        with patch("ai_model.forecast_worker.acquire_worker_lock", side_effect=[False, True]) as lock_mock, \
             patch.object(simulation, "clear_history", return_value={"readings_deleted": 120, "forecasts_deleted": 1}) as clear_mock:
            result = simulation.clear_simulated_data(
                connection_factory=lambda: conn,
                report=reports.append,
                retries=3,
                retry_delay=0.25,
                sleeper=sleeps.append,
            )

        self.assertEqual(lock_mock.call_count, 2)
        self.assertEqual(sleeps, [0.25])
        clear_mock.assert_called_once_with(conn)
        self.assertEqual(conn.commit.call_count, 2)
        self.assertEqual(result, {"readings_deleted": 120, "forecasts_deleted": 1})
        self.assertEqual(reports, [{"status": "cleared", "readings_deleted": 120, "forecasts_deleted": 1}])
        unlock_sql = cursor.execute.call_args.args[0]
        self.assertIn("pg_advisory_unlock", unlock_sql)

    def test_clear_simulated_data_raises_when_worker_lock_remains_busy(self):
        conn = MagicMock()
        with patch("ai_model.forecast_worker.acquire_worker_lock", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "prediction is running"):
                simulation.clear_simulated_data(
                    connection_factory=lambda: conn,
                    retries=2,
                    retry_delay=0,
                )
        conn.close.assert_called_once_with()

    def test_clear_simulated_data_rolls_back_and_unlocks_on_failure(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        with patch("ai_model.forecast_worker.acquire_worker_lock", return_value=True), \
             patch.object(simulation, "clear_history", side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                simulation.clear_simulated_data(connection_factory=lambda: conn, retries=1)
        conn.rollback.assert_called_once_with()
        unlock_sql = cursor.execute.call_args.args[0]
        self.assertIn("pg_advisory_unlock", unlock_sql)


class StandaloneSimClientTests(unittest.TestCase):
    def test_client_constants_stay_in_sync_with_ai_model(self):
        self.assertEqual(sim_client.FEATURE_COLUMNS, predictor.FEATURE_COLUMNS)
        self.assertEqual(sim_client.LOOKBACK_SAMPLES, predictor.LOOKBACK_SAMPLES)
        self.assertEqual(sim_client.PROFILES, simulation.PROFILES)
        self.assertEqual(sim_client.RANDOM_BANDS, simulation.RANDOM_BANDS)
        self.assertEqual(sim_client.WORKER_LOCK_ID, forecast_worker.WORKER_LOCK_ID)
        self.assertEqual(sim_client.MANUAL_SEEDER_LOCK, simulation.MANUAL_SEEDER_LOCK)

    def test_resolve_host_precedence(self):
        self.assertEqual(sim_client.resolve_host("192.168.1.50", {"DB_HOST": "10.0.0.2"}), "192.168.1.50")
        self.assertEqual(sim_client.resolve_host(None, {"DB_HOST": "10.0.0.2"}), "10.0.0.2")
        self.assertEqual(sim_client.resolve_host(None, {}), "192.168.11.50")
        self.assertEqual(sim_client.resolve_host("   ", {"DB_HOST": ""}), "192.168.11.50")

    def test_configure_db_env_sets_defaults_without_overwriting_overrides(self):
        env = {"DB_PORT": "15432", "DB_PASS": "custompass"}
        sim_client.configure_db_env("192.168.1.44", user="customuser", env=env)
        self.assertEqual(env["DB_HOST"], "192.168.1.44")
        self.assertEqual(env["DB_PORT"], "15432")
        self.assertEqual(env["DB_NAME"], "air_quality")
        self.assertEqual(env["DB_USER"], "customuser")
        self.assertEqual(env["DB_PASS"], "custompass")

    def test_check_db_reachable_reports_clear_network_guidance_on_failure(self):
        sock = MagicMock()
        with patch.object(sim_client.socket, "create_connection", return_value=sock) as opener:
            sim_client.check_db_reachable("192.168.11.50", "5432", opener=opener)
        opener.assert_called_once_with(("192.168.11.50", 5432), timeout=4.0)

        with self.assertRaisesRegex(RuntimeError, "Cannot reach server at 192.168.11.50:5432"):
            sim_client.check_db_reachable(
                "192.168.11.50",
                "5432",
                opener=MagicMock(side_effect=OSError("timed out")),
            )

    def test_ensure_psycopg2_skips_venv_when_already_installed(self):
        with patch.object(sim_client.importlib.util, "find_spec", return_value=object()):
            self.assertIsNone(sim_client.ensure_psycopg2("192.168.11.50", "good"))

    def test_ensure_psycopg2_creates_venv_and_delegates_when_missing(self):
        fake_venv = Path("/cache/sim-venv")
        with patch.object(sim_client.importlib.util, "find_spec", return_value=None), \
             patch.object(sim_client, "_cache_venv_dir", return_value=fake_venv), \
             patch.object(sim_client.venv, "create") as venv_create, \
             patch.object(
                 sim_client.subprocess,
                 "run",
                 side_effect=[SimpleNamespace(returncode=1), SimpleNamespace(returncode=0), SimpleNamespace(returncode=0)],
             ) as run_mock:
            code = sim_client.ensure_psycopg2("192.168.11.50", "delete", port="5432")

        self.assertEqual(code, 0)
        venv_create.assert_called_once_with(fake_venv, with_pip=True)
        install_cmd = run_mock.call_args_list[1].args[0]
        self.assertIn(sim_client.PSYCOPG2_REQUIREMENT, install_cmd)
        exec_cmd = run_mock.call_args_list[2].args[0]
        self.assertIn("--bootstrapped", exec_cmd)
        self.assertIn("delete", exec_cmd)
        self.assertIn("192.168.11.50", exec_cmd)

    def test_run_action_dispatches_delete_and_profiles(self):
        with patch.object(sim_client, "clear_simulated_data") as clear_mock:
            self.assertEqual(sim_client.run_action("delete"), 0)
        clear_mock.assert_called_once()

        with patch.object(sim_client, "run_simulation") as sim_run:
            self.assertEqual(sim_client.run_action("mid"), 0)
        sim_run.assert_called_once_with("mid")

    def test_simulation_scripts_folder_works_standalone_outside_repo(self):
        scripts_dir = Path(__file__).resolve().parent.parent / "simulation_scripts"
        expected = {
            "simulate_good.sh": "good",
            "simulate_mid.sh": "mid",
            "simulate_bad.sh": "bad",
            "simulate_random.sh": "random",
            "delete_simulated_data.sh": "delete",
        }
        self.assertEqual({p.name for p in scripts_dir.glob("*.sh")}, set(expected))
        self.assertTrue((scripts_dir / "README.md").is_file())
        self.assertTrue((scripts_dir / ".sim_client.py").is_file())
        for filename, action in expected.items():
            raw = (scripts_dir / filename).read_bytes()
            self.assertNotIn(b"\r", raw, f"{filename} must use LF line endings")
            text = raw.decode("utf-8")
            self.assertTrue(text.startswith("#!/usr/bin/env bash\n"))
            self.assertIn(f".sim_client.py {action} ", text)
            self.assertNotIn("ai_model", text)

        with tempfile.TemporaryDirectory() as tmpdir:
            copied_dir = Path(tmpdir) / "simulation_scripts"
            shutil.copytree(scripts_dir, copied_dir)
            result = subprocess.run(
                [sys.executable, str(copied_dir / ".sim_client.py"), "--help"],
                cwd=str(copied_dir),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("192.168.11.50", result.stdout)


if __name__ == "__main__":
    unittest.main()
