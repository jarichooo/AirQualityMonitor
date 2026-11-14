import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ai_model import lan_runner, simulation


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


class LanRunnerTests(unittest.TestCase):
    def test_resolve_host_precedence(self):
        self.assertEqual(lan_runner.resolve_host("192.168.1.50", {"DB_HOST": "10.0.0.2"}), "192.168.1.50")
        self.assertEqual(lan_runner.resolve_host(None, {"DB_HOST": "10.0.0.2"}), "10.0.0.2")
        self.assertEqual(
            lan_runner.resolve_host(None, {}, interactive=True, input_fn=lambda _: " 192.168.1.88 "),
            "192.168.1.88",
        )
        self.assertEqual(
            lan_runner.resolve_host(None, {}, interactive=True, input_fn=lambda _: ""),
            "127.0.0.1",
        )
        self.assertEqual(lan_runner.resolve_host(None, {}, interactive=False), "127.0.0.1")

    def test_configure_db_env_sets_defaults_without_overwriting_overrides(self):
        env = {"DB_PORT": "15432", "DB_PASS": "custompass"}
        lan_runner.configure_db_env("192.168.1.44", user="customuser", env=env)
        self.assertEqual(env["DB_HOST"], "192.168.1.44")
        self.assertEqual(env["DB_PORT"], "15432")
        self.assertEqual(env["DB_NAME"], "air_quality")
        self.assertEqual(env["DB_USER"], "customuser")
        self.assertEqual(env["DB_PASS"], "custompass")

    def test_check_db_reachable_reports_clear_network_guidance_on_failure(self):
        sock = MagicMock()
        with patch("ai_model.lan_runner.socket.create_connection", return_value=sock) as opener:
            lan_runner.check_db_reachable("192.168.1.50", "5432", opener=opener)
        opener.assert_called_once_with(("192.168.1.50", 5432), timeout=4.0)

        with self.assertRaisesRegex(RuntimeError, "Cannot reach PostgreSQL at 192.168.1.50:5432"):
            lan_runner.check_db_reachable(
                "192.168.1.50",
                "5432",
                opener=MagicMock(side_effect=OSError("timed out")),
            )

    def test_ensure_psycopg2_skips_venv_when_already_installed(self):
        with patch.object(lan_runner.importlib.util, "find_spec", return_value=object()):
            self.assertIsNone(lan_runner.ensure_psycopg2(Path("."), "127.0.0.1", "good"))

    def test_ensure_psycopg2_creates_venv_and_delegates_when_missing(self):
        fake_root = Path("/repo")
        fake_venv = Path("/cache/sim-venv")
        with patch.object(lan_runner.importlib.util, "find_spec", return_value=None), \
             patch.object(lan_runner, "_cache_venv_dir", return_value=fake_venv), \
             patch.object(lan_runner.venv, "create") as venv_create, \
             patch.object(
                 lan_runner.subprocess,
                 "run",
                 side_effect=[SimpleNamespace(returncode=1), SimpleNamespace(returncode=0), SimpleNamespace(returncode=0)],
             ) as run_mock:
            code = lan_runner.ensure_psycopg2(fake_root, "192.168.1.50", "delete", port="5432")

        self.assertEqual(code, 0)
        venv_create.assert_called_once_with(fake_venv, with_pip=True)
        install_cmd = run_mock.call_args_list[1].args[0]
        self.assertIn(lan_runner.PSYCOPG2_REQUIREMENT, install_cmd)
        exec_cmd = run_mock.call_args_list[2].args[0]
        self.assertIn("--bootstrapped", exec_cmd)
        self.assertIn("delete", exec_cmd)
        self.assertIn("192.168.1.50", exec_cmd)

    def test_run_action_dispatches_delete_and_profiles(self):
        with patch.object(simulation, "clear_simulated_data") as clear_mock:
            self.assertEqual(lan_runner.run_action("delete"), 0)
        clear_mock.assert_called_once()

        with patch.object(simulation, "main", return_value=0) as sim_main:
            self.assertEqual(lan_runner.run_action("mid"), 0)
        sim_main.assert_called_once_with("mid", [])

    def test_simulation_scripts_folder_has_five_lf_bash_scripts(self):
        scripts_dir = Path(__file__).resolve().parent.parent / "simulation_scripts"
        expected = {
            "simulate_good.sh": "good",
            "simulate_mid.sh": "mid",
            "simulate_bad.sh": "bad",
            "simulate_random.sh": "random",
            "delete_simulated_data.sh": "delete",
        }
        actual_names = {p.name for p in scripts_dir.iterdir()}
        self.assertEqual(actual_names, set(expected))
        for filename, action in expected.items():
            raw = (scripts_dir / filename).read_bytes()
            self.assertNotIn(b"\r", raw, f"{filename} must use LF line endings")
            text = raw.decode("utf-8")
            self.assertTrue(text.startswith("#!/usr/bin/env bash\n"))
            self.assertIn(f"-m ai_model.lan_runner {action} ", text)


if __name__ == "__main__":
    unittest.main()
