import csv
import io
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import app as application


class AppTestCase(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, SECRET_KEY="test")
        self.client = application.app.test_client()

    def login(self):
        with self.client.session_transaction() as session:
            session.update(logged_in=True, username="admin", role="admin")

    def test_authenticated_data_page_and_export(self):
        self.assertEqual(self.client.get("/data").status_code, 302)
        self.login()
        with patch.object(application, "get_db_connection") as connect:
            connect.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = (0, None, None)
            connect.return_value.cursor.return_value.__enter__.return_value.fetchall.return_value = []
            dashboard = self.client.get("/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Good day", dashboard.data)
        self.assertIn(b"Methane", dashboard.data)
        self.assertIn(b"Ammonia", dashboard.data)
        self.assertIn("mq4_ppm", application.READING_COLUMNS)
        self.assertIn("mq137_ppm", application.READING_COLUMNS)
        self.assertNotIn("mq135_raw", application.READING_COLUMNS)
        self.assertNotIn("mq137_raw", application.READING_COLUMNS)

        row = (datetime(2026, 9, 27, 5, 50, 25, tzinfo=timezone.utc), 31.5, 68, 420, 8.0, 15.2, 22.0, 12, 32)
        with patch.object(application, "fetch_readings", return_value=([row], 1, None)):
            page = self.client.get("/data?date_range=all&per_page=25")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Timestamp (PHT)", page.data)
            self.assertIn(b"Sep 27, 2026, 01:50:25 PM", page.data)
            self.assertIn(b'class="numeric cell-normal is-normal">31.5</td>', page.data)
            self.assertIn(b'class="numeric cell-warning is-warning">12</td>', page.data)
            self.assertIn(b"31.5", page.data)
            self.assertIn(b"CO2 (ppm)", page.data)
            self.assertIn(b"420", page.data)
            self.assertIn(b"Status", page.data)
            self.assertIn(b"status-column", page.data)
            self.assertIn(b"severity-pill is-warning", page.data)
            self.assertIn(b"WARNING", page.data)
            self.assertIn(b"Sensor Readings", page.data)
            self.assertIn(b"data-custom-select", page.data)
            self.assertIn(b"panel-heading-tools", page.data)
            self.assertIn(b"Export", page.data)
            self.assertNotIn(b"page-heading-actions", page.data)
            self.assertNotIn(b'name="per_page"', page.data)
            self.assertNotIn(b">Apply<", page.data)
            self.assertNotIn(b">Clear<", page.data)
            self.assertIn(b'aria-label="Log out"', page.data)
            self.assertNotIn(b">Logout<", page.data)
            self.assertNotIn(b"profile-identity", page.data)
            self.assertNotIn(b"profile-button", page.data)
            self.assertNotIn(b"profile-popover", page.data)

            export = self.client.get("/data/export.csv?date_range=all")
            self.assertEqual(export.status_code, 200)
            self.assertIn(b"Timestamp (UTC),Temp", export.data)
            self.assertIn(b"2026-09-27 05:50:25", export.data)
            csv_rows = list(csv.reader(io.StringIO(export.data.decode())))
            self.assertEqual(csv_rows[0][-5:], ["PM1.0 (µg/m³)", "PM2.5 (µg/m³)", "PM10 (µg/m³)", "Methane (ppm)", "Ammonia (ppm)"])
            self.assertEqual(csv_rows[1][-5:], ["8.0", "15.2", "22.0", "12", "32"])
            self.assertIn(b"Methane (ppm)", page.data)
            self.assertIn(b"Ammonia (ppm)", page.data)
            self.assertNotIn(b"MQ135", page.data)
            self.assertNotIn(b"(raw)", page.data)

    def test_data_table_status_column_severities(self):
        self.login()
        now = datetime(2026, 11, 9, 8, 0, 0, tzinfo=timezone.utc)
        normal_row = (now, 25.0, 60.0, 800, 5.0, 20.0, 50.0, 5.0, 500.0)
        warning_row = (now, 33.0, 60.0, 800, 5.0, 20.0, 50.0, 5.0, 500.0)
        critical_row = (now, 36.0, 60.0, 800, 5.0, 20.0, 50.0, 5.0, 500.0)
        with patch.object(application, "fetch_readings", return_value=([normal_row, warning_row, critical_row], 3, None)):
            page = self.client.get("/data")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b'<th class="status-column-header">Status</th>', page.data)
            self.assertIn(b'<span class="severity-pill is-normal">NORMAL</span>', page.data)
            self.assertIn(b'<span class="severity-pill is-warning">WARNING</span>', page.data)
            self.assertIn(b'<span class="severity-pill is-critical">CRITICAL</span>', page.data)
            self.assertIn(b"Nov 9, 2026, 04:00:00 PM", page.data)
            self.assertIn(b'class="numeric cell-normal is-normal">25.0</td>', page.data)
            self.assertIn(b'class="numeric cell-warning is-warning">33.0</td>', page.data)
            self.assertIn(b'class="numeric cell-critical is-critical">36.0</td>', page.data)

    def test_reading_filters_and_clauses(self):
        for key, clause in (
            ("warning_critical", "temperature_c < 21"),
            ("critical_only", "temperature_c < 18"),
            ("ammonia", "mq137_ppm > 1000"),
            ("methane", "mq4_ppm > 10"),
            ("temperature", "temperature_c < 21"),
            ("humidity", "humidity_perc < 50"),
            ("co2", "co2_ppm > 2500"),
            ("pm1", "pm1_ugm3 > 10"),
            ("pm25", "pm25_ugm3 > 35"),
            ("pm10", "pm10_ugm3 > 150"),
        ):
            f = application.parse_filters({"filter": key})
            self.assertEqual(f["filter"], key)
            where, _, _ = application.build_where_clause(f)
            self.assertIn(clause, where)

        self.login()
        with patch.object(application, "fetch_readings", return_value=([], 0, None)):
            page = self.client.get("/data?filter=critical_only")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Critical only", page.data)
            self.assertIn(b"MQ-137 Ammonia outside normal", page.data)
            self.assertIn(b"MH-Z19E Carbon Dioxide outside normal", page.data)
            self.assertIn(b'name="filter" value="critical_only"', page.data)

    def test_custom_date_validation(self):
        filters = application.parse_filters({
            "date_range": "custom",
            "start_date": "2026-09-30",
            "end_date": "2026-09-01",
        })
        _, _, error = application.build_where_clause(filters)
        self.assertIsNotNone(error)

    def test_delete_readings_requires_admin_token_and_confirmation(self):
        with patch.object(application, "get_db_connection") as connect:
            self.assertEqual(self.client.post("/data/delete").location, "/login")
            self.login()
            self.assertEqual(self.client.get("/data/delete").status_code, 405)
            self.assertEqual(self.client.post("/data/delete").status_code, 400)
            with self.client.session_transaction() as session:
                session["delete_readings_token"] = "test-token"
            for fields in ({"csrf_token": "wrong", "delete_mode": "all", "confirmation": "delete_all"},
                           {"csrf_token": "test-token"},
                           {"csrf_token": "test-token", "delete_mode": "all", "confirmation": "cancel"},
                           {"csrf_token": "test-token", "delete_mode": "from_timestamp",
                            "confirmation": "delete_all", "delete_scope": "simulated",
                            "delete_from": "2026-10-30T12:00"},
                           {"csrf_token": "\u00e9", "delete_mode": "all", "confirmation": "delete_all"}):
                self.assertEqual(self.client.post("/data/delete", data=fields).status_code, 400)
            with self.client.session_transaction() as session:
                session["role"] = "viewer"
            self.assertEqual(self.client.post("/data/delete", data={
                "csrf_token": "test-token", "delete_mode": "all", "confirmation": "delete_all"
            }).status_code, 403)
            connect.assert_not_called()

    def test_delete_all_clears_forecasts_and_resets_identity(self):
        self.login()
        with self.client.session_transaction() as session:
            session["delete_readings_token"] = "test-token"
        fields = {"csrf_token": "test-token", "delete_mode": "all", "confirmation": "delete_all"}
        with patch.object(application, "get_db_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.side_effect = [(True,), ("air_quality_forecasts",)]
            response = self.client.post("/data/delete", data=fields)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.location, "/data")
            conn = connect.return_value
            commands = [call.args[0] for call in cursor.execute.call_args_list]
            self.assertEqual(commands, [
                "SET LOCAL lock_timeout = '5s'",
                "SELECT pg_try_advisory_xact_lock(%s)",
                "SELECT to_regclass('public.air_quality_forecasts')",
                "LOCK TABLE air_quality_forecasts IN ACCESS EXCLUSIVE MODE",
                "LOCK TABLE air_quality_logs IN ACCESS EXCLUSIVE MODE",
                "TRUNCATE TABLE air_quality_forecasts",
                "TRUNCATE TABLE air_quality_logs RESTART IDENTITY",
            ])
            self.assertEqual(cursor.execute.call_args_list[1].args[1], (application.DELETE_WORKER_LOCK_ID,))
            conn.__exit__.assert_called_once_with(None, None, None)
            conn.close.assert_called_once()
        with patch.object(application, "fetch_readings", return_value=([], 0, None)):
            page = self.client.get("/data").data
            self.assertIn(b"next reading ID is 1", page)
            self.assertIn(b"saved forecasts were deleted", page)

    def test_delete_from_timestamp_preserves_ids_and_restarts_after_retained_max(self):
        self.login()
        with self.client.session_transaction() as session:
            session["delete_readings_token"] = "test-token"
        fields = {
            "csrf_token": "test-token",
            "delete_mode": "from_timestamp",
            "delete_scope": "simulated",
            "delete_from": "2026-10-30T12:34:56",
            "confirmation": "delete_from_timestamp",
        }
        with patch.object(application, "get_db_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.side_effect = [
                (True,),
                ("air_quality_forecasts",),
                (2, ["sim-farm-good"]),
                ("public", "air_quality_logs_id_seq", 32322),
            ]
            response = self.client.post("/data/delete", data=fields)
            self.assertEqual(response.status_code, 303)

            calls = cursor.execute.call_args_list
            delete_call = calls[5]
            self.assertIn("recorded_at >= %s AND device_id = ANY(%s)", delete_call.args[0])
            self.assertEqual(delete_call.args[1], (
                datetime(2026, 10, 30, 12, 34, 56, tzinfo=timezone.utc),
                list(application.SIMULATED_DEVICE_IDS),
            ))
            self.assertIn("sim-farm-series", delete_call.args[1][1])
            self.assertEqual(calls[6].args, (
                "DELETE FROM air_quality_forecasts WHERE device_id = ANY(%s)",
                (["sim-farm-good"],),
            ))
            self.assertIn("Identifier('public', 'air_quality_logs_id_seq')", repr(calls[-1].args[0]))
            self.assertIn("Literal(32322)", repr(calls[-1].args[0]))
            self.assertFalse(any("UPDATE air_quality_logs" in str(call.args[0]) for call in calls))
        with patch.object(application, "fetch_readings", return_value=([], 0, None)):
            page = self.client.get("/data").data
            self.assertIn(b"Deleted 2 matching readings", page)
            self.assertIn(b"Existing IDs were kept", page)
            self.assertIn(b"next reading ID is 32322", page)

    def test_delete_from_timestamp_all_scope_handles_empty_retained_table_without_forecasts(self):
        self.login()
        with self.client.session_transaction() as session:
            session["delete_readings_token"] = "test-token"
        fields = {
            "csrf_token": "test-token",
            "delete_mode": "from_timestamp",
            "delete_scope": "all",
            "delete_from": "2026-10-30T12:34",
            "confirmation": "delete_from_timestamp",
        }
        with patch.object(application, "get_db_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.side_effect = [
                (True,), (None,), (3, ["physical-node"]), ("public", "air_quality_logs_id_seq", 1)
            ]
            self.client.post("/data/delete", data=fields)
            calls = cursor.execute.call_args_list
            self.assertIn("recorded_at >= %s RETURNING device_id", calls[4].args[0])
            self.assertEqual(calls[4].args[1], (datetime(2026, 10, 30, 12, 34, tzinfo=timezone.utc),))
            self.assertFalse(any("DELETE FROM air_quality_forecasts" in str(call.args[0]) for call in calls))
            self.assertIn("Literal(1)", repr(calls[-1].args[0]))

    def test_delete_all_simulated_clears_simulated_rows_and_restarts_sequence(self):
        self.login()
        with self.client.session_transaction() as session:
            session["delete_readings_token"] = "test-token"
        fields = {
            "csrf_token": "test-token",
            "delete_mode": "all_simulated",
            "confirmation": "delete_all_simulated",
        }
        with patch.object(application, "get_db_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.side_effect = [
                (True,), ("air_quality_forecasts",), (125, ["sim-farm-good"]), ("public", "air_quality_logs_id_seq", 9861)
            ]
            response = self.client.post("/data/delete", data=fields)
            self.assertEqual(response.status_code, 303)
            calls = cursor.execute.call_args_list
            self.assertIn("WHERE device_id = ANY(%s) RETURNING device_id", calls[5].args[0])
            self.assertEqual(calls[5].args[1], (list(application.SIMULATED_DEVICE_IDS),))
            self.assertIn("Literal(9861)", repr(calls[-1].args[0]))
        with patch.object(application, "fetch_readings", return_value=([], 0, None)):
            page = self.client.get("/data").data
            self.assertIn(b"Deleted 125 simulation readings", page)
            self.assertIn(b"next reading ID is 9861", page)

    def test_delete_incomplete_clears_short_device_runs_and_restarts_sequence(self):
        self.login()
        with self.client.session_transaction() as session:
            session["delete_readings_token"] = "test-token"
        fields = {
            "csrf_token": "test-token",
            "delete_mode": "incomplete",
            "confirmation": "delete_incomplete",
        }
        with patch.object(application, "get_db_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.side_effect = [
                (True,), ("air_quality_forecasts",), (21, ["esp32-68EE8FDD25F0"]), ("public", "air_quality_logs_id_seq", 9861)
            ]
            response = self.client.post("/data/delete", data=fields)
            self.assertEqual(response.status_code, 303)
            calls = cursor.execute.call_args_list
            self.assertIn("HAVING COUNT(DISTINCT date_trunc('minute', recorded_at AT TIME ZONE 'UTC')) < 120", calls[5].args[0])
            self.assertEqual(calls[5].args[1], ())
            self.assertIn("DELETE FROM public.air_quality_prediction_requests", calls[6].args[0])
            self.assertEqual(calls[7].args[0], "DELETE FROM air_quality_forecasts")
            self.assertIn("Literal(9861)", repr(calls[-1].args[0]))
        with patch.object(application, "fetch_readings", return_value=([], 0, None)):
            page = self.client.get("/data").data
            self.assertIn(b"Deleted 21 incomplete readings", page)
            self.assertIn(b"next reading ID is 9861", page)

    def test_delete_from_timestamp_no_match_does_not_rewind_sequence_or_forecasts(self):
        self.login()
        with self.client.session_transaction() as session:
            session["delete_readings_token"] = "test-token"
        fields = {
            "csrf_token": "test-token", "delete_mode": "from_timestamp", "delete_scope": "simulated",
            "delete_from": "2026-10-30T12:34", "confirmation": "delete_from_timestamp",
        }
        with patch.object(application, "get_db_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.side_effect = [(True,), (None,), (0, [])]
            self.client.post("/data/delete", data=fields)
            commands = [call.args[0] for call in cursor.execute.call_args_list]
            self.assertFalse(any(command.startswith(("DELETE FROM air_quality_forecasts", "TRUNCATE"))
                                 for command in commands))
            self.assertFalse(any("ALTER SEQUENCE" in command for command in commands))
        with patch.object(application, "fetch_readings", return_value=([], 0, None)):
            self.assertIn(b"No data or ID sequence was changed", self.client.get("/data").data)

    def test_delete_timestamp_rejects_ambiguous_or_hostile_values(self):
        self.login()
        with self.client.session_transaction() as session:
            session["delete_readings_token"] = "test-token"
        base = {
            "csrf_token": "test-token", "delete_mode": "from_timestamp", "delete_scope": "simulated",
            "confirmation": "delete_from_timestamp",
        }
        with patch.object(application, "get_db_connection") as connect:
            for value in (None, "", "2026-10-30", "2026-10-30T12:34Z", "2026-10-30T12:34+08:00",
                          "2026-02-30T12:34", "2026-10-30T12:34; DROP TABLE air_quality_logs"):
                self.assertEqual(self.client.post("/data/delete", data={**base, "delete_from": value}).status_code, 400)
            self.assertEqual(self.client.post("/data/delete", data={
                **base, "delete_scope": "simulated' OR TRUE --", "delete_from": "2026-10-30T12:34"
            }).status_code, 400)
            self.assertEqual(self.client.post("/data/delete", data={
                "csrf_token": "test-token", "delete_mode": "all", "delete_scope": "simulated",
                "confirmation": "delete_all",
            }).status_code, 400)
            connect.assert_not_called()

    def test_delete_resolves_renamed_sequence_and_rolls_back_a_restart_failure(self):
        self.login()
        with self.client.session_transaction() as session:
            session["delete_readings_token"] = "test-token"
        fields = {
            "csrf_token": "test-token", "delete_mode": "from_timestamp", "delete_scope": "simulated",
            "delete_from": "2026-10-30T12:34", "confirmation": "delete_from_timestamp",
        }
        for missing_sequence in (False, True):
            with self.subTest(missing_sequence=missing_sequence), patch.object(application, "get_db_connection") as connect:
                conn = connect.return_value
                cursor = conn.cursor.return_value.__enter__.return_value
                cursor.fetchone.side_effect = [
                    (True,), (None,), (2, ["sim-farm-good"]),
                    None if missing_sequence else ("public", 'renamed.sequence"name', 50001),
                ]

                def fail_restart(statement, *params):
                    if isinstance(statement, application.sql.Composed):
                        self.assertIn(application.sql.Identifier("public", 'renamed.sequence"name'), list(statement))
                        raise application.psycopg2.OperationalError("restart failed")

                cursor.execute.side_effect = fail_restart
                with self.assertLogs(application.app.logger, level="ERROR"):
                    self.assertEqual(self.client.post("/data/delete", data=fields).status_code, 303)
                self.assertTrue(issubclass(conn.__exit__.call_args.args[0], application.psycopg2.Error))
                conn.close.assert_called_once()
            with patch.object(application, "fetch_readings", return_value=([], 0, None)):
                page = self.client.get("/data").data
                self.assertIn(b"No changes were committed", page)
                self.assertNotIn(b"Deleted 2 matching readings", page)

    def test_delete_refuses_busy_worker_and_reports_database_rollback(self):
        self.login()
        with self.client.session_transaction() as session:
            session["delete_readings_token"] = "test-token"
        fields = {"csrf_token": "test-token", "delete_mode": "all", "confirmation": "delete_all"}
        with patch.object(application, "get_db_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = (False,)
            self.client.post("/data/delete", data=fields)
            self.assertEqual([call.args[0] for call in cursor.execute.call_args_list], [
                "SET LOCAL lock_timeout = '5s'", "SELECT pg_try_advisory_xact_lock(%s)"
            ])
        with patch.object(application, "fetch_readings", return_value=([], 0, None)):
            page = self.client.get("/data").data
            self.assertIn(b"prediction or simulation is active", page)
            self.assertNotIn(b"worker", page)

        with patch.object(application, "get_db_connection") as connect:
            conn = connect.return_value
            cursor = conn.cursor.return_value.__enter__.return_value
            cursor.fetchone.side_effect = [(True,), (None,)]
            cursor.execute.side_effect = [None, None, None, application.psycopg2.OperationalError("locked")]
            with self.assertLogs(application.app.logger, level="ERROR"):
                self.assertEqual(self.client.post("/data/delete", data=fields).status_code, 303)
            self.assertIs(conn.__exit__.call_args.args[0], application.psycopg2.OperationalError)
            conn.close.assert_called_once()
        with patch.object(application, "fetch_readings", return_value=([], 0, None)):
            page = self.client.get("/data")
            self.assertIn(b"No changes were committed", page.data)
            self.assertNotIn(b"next reading ID is 1", page.data)

    def test_delete_button_and_token_are_only_available_to_admins(self):
        self.login()
        with patch.object(application, "fetch_readings", return_value=([], 0, None)):
            page = self.client.get("/data?q=hidden")
            self.assertIn(b'id="delete-all-readings"', page.data)
            self.assertIn(b'id="delete-readings-dialog"', page.data)
            self.assertIn(b'id="delete-readings-form"', page.data)
            self.assertIn(b'name="delete_mode"', page.data)
            self.assertIn(b'name="delete_from"', page.data)
            self.assertIn(b'name="delete_scope"', page.data)
            self.assertIn(b'name="confirmation" value=""', page.data)
            with self.client.session_transaction() as session:
                self.assertIn(session["delete_readings_token"].encode(), page.data)
                session["role"] = "viewer"
            page = self.client.get("/data")
            self.assertNotIn(b'id="delete-all-readings"', page.data)
            self.assertNotIn(b'id="delete-readings-form"', page.data)

    def test_collection_summary_empty_populated_and_unavailable(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.login()
        earliest = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)
        latest = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        for result, expected in (((0, None, None), b"No readings yet"),
                                 ((3, earliest, latest), b"<strong>3</strong> stored readings")):
            with patch.object(application, "get_db_connection") as connect:
                cursor = connect.return_value.cursor.return_value.__enter__.return_value
                cursor.fetchone.side_effect = [result, (latest, 30) + (None,) * 7,
                                               (None, None, None, 0) * 8]
                cursor.fetchall.return_value = []
                page = self.client.get("/dashboard")
                self.assertEqual(page.status_code, 200)
                self.assertIn(b'id="device-live-dot"', page.data)
                if result[0]:
                    self.assertNotIn(b"2026-10-01 10:00:00 to 2026-10-01 12:00:00", page.data)
                connect.return_value.close.assert_called_once()
        with patch.object(application, "get_db_connection", side_effect=application.psycopg2.OperationalError):
            self.assertIn(b"Collection status is unavailable", self.client.get("/dashboard").data)

    def test_empty_data_and_database_failure(self):
        self.login()
        with patch.object(application, "fetch_readings", return_value=([], 0, None)):
            self.assertIn(b"No readings yet", self.client.get("/data").data)
            export = self.client.get("/data/export.csv")
            self.assertEqual(len(export.data.decode().splitlines()), 1)
        with patch.object(application, "fetch_readings", side_effect=application.psycopg2.OperationalError):
            self.assertIn(b"Sensor data is unavailable", self.client.get("/data").data)
            self.assertEqual(self.client.get("/data/export.csv").status_code, 503)

    def test_missing_login_fields_and_export_authentication(self):
        self.assertEqual(self.client.post("/login", data={}).status_code, 400)
        self.assertEqual(self.client.get("/data/export.csv").status_code, 302)

    def test_root_always_opens_login_and_login_redirects_to_dashboard(self):
        for authenticated in (False, True):
            if authenticated:
                self.login()
            response = self.client.get("/", follow_redirects=True)
            self.assertEqual(response.request.path, "/login")
            self.assertIn(b'name="password"', response.data)
        with patch.object(application, "get_db_connection") as connect, patch.object(application, "check_password_hash", return_value=True):
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = {"password_hash": "hash", "role": "admin"}
            response = self.client.post("/login", data={"username": "admin", "password": "password"})
            self.assertEqual(response.location, "/dashboard")
        self.client.get("/logout")
        self.assertEqual(self.client.get("/dashboard").location, "/login")

    def test_analytics_and_missing_hour_gaps(self):
        self.login()
        earliest = datetime(2026, 10, 4, 5, 36, tzinfo=timezone.utc)
        latest = datetime(2026, 10, 26, 23, 59, tzinfo=timezone.utc)
        chart_start = earliest.replace(minute=0)
        series = [(chart_start, 30), (chart_start + timedelta(hours=1), 32),
                  (chart_start + timedelta(hours=3), 31)]
        with patch.object(application, "get_db_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.side_effect = [(32320, earliest, latest), (latest, 30) + (None,) * 7,
                                          (30, 31, 32, 3) + (None, None, None, 0) * 7]
            cursor.fetchall.side_effect = [series, [(latest - timedelta(minutes=1), 30, "node-1"),
                                                   (latest, 32, "node-1")]]
            page = self.client.get("/dashboard?sensor=bad")
            self.assertEqual(page.status_code, 200)
            self.assertNotIn(b"Live readings, trends, and forecasts", page.data)
            self.assertNotIn(b"2026-10-04 05:36:00 to 2026-10-26 23:59:00", page.data)
            self.assertNotIn(b"Hourly averages", page.data)
            self.assertNotIn(b"Last 24 hours (UTC)", page.data)
            self.assertIn(b"30.00", page.data)
            self.assertIn(b"trend-threshold", page.data)
            self.assertNotIn(b"trend-threshold-label", page.data)
            self.assertIn(b'id="dashboard-notification-slot"', page.data)
            for col in ("mq137_ppm", "mq4_ppm", "temperature_c", "humidity_perc", "pm1_ugm3", "pm25_ugm3", "pm10_ugm3", "co2_ppm"):
                self.assertIn(f'id="chart-{col}"'.encode(), page.data)
            self.assertNotIn(b"trend-point", page.data)
            self.assertNotIn(b"View hourly values", page.data)
            self.assertIn(b"2-hour forecast", page.data)
            self.assertIn(b'id="forecast-panel"', page.data)
            self.assertIn(b"forecasts.js", page.data)
            self.assertNotIn(b"About these analytics", page.data)
            self.assertNotIn(b"analytics-notes-title", page.data)
            self.assertIn(b"<polyline", page.data)
            self.assertIn(b'<h2 id="recorded-trend-title">Temperature and Humidity</h2>', page.data)
            self.assertNotIn(b"Individual measurements", page.data)
            self.assertNotIn(b"View recorded values", page.data)
            self.assertIn("AVG(temperature_c)", cursor.execute.call_args_list[-2].args[0])
            self.assertIn("SELECT recorded_at, temperature_c, device_id", cursor.execute.call_args.args[0])
            self.assertEqual(cursor.execute.call_args_list[-3].args[1], (earliest, latest))
            self.assertEqual(cursor.execute.call_args_list[-2].args[1], (earliest, latest))
            self.assertEqual(cursor.execute.call_args.args[1], (latest - timedelta(hours=1), latest))
        trend = application.build_trend(series, chart_start, datetime(2026, 10, 27, tzinfo=timezone.utc))
        self.assertEqual(len(trend["segments"]), 2)
        self.assertEqual(len(trend["points"]), 3)
        self.assertFalse(application.build_trend([(chart_start, None)], chart_start)["points"])

    def test_recorded_graph_preserves_time_gaps_and_device_boundaries(self):
        start = datetime(2026, 10, 4, 6, tzinfo=timezone.utc)
        rows = [(start, 30, "a"), (start + timedelta(minutes=1), 31, "a"),
                (start + timedelta(minutes=10), 32, "a"),
                (start + timedelta(minutes=10), 29, "b"),
                (start + timedelta(minutes=11), None, "b"),
                (start + timedelta(minutes=12), 30, "b")]
        trend = application.build_trend(rows, start, hourly=False)
        self.assertEqual(len(trend["points"]), 5)
        self.assertEqual(len(trend["segments"]), 4)
        self.assertEqual(trend["points"][0]["x"], 60)
        self.assertEqual(trend["points"][1]["x"], round(60 + 700 / 60, 2))
        self.assertEqual(trend["points"][3]["device"], "b")
        self.assertFalse(application.build_trend([(start, None, "a")], start, hourly=False)["points"])

    def test_compact_numbered_pagination(self):
        self.assertEqual(application.build_pagination_items(1, 1), [1])
        self.assertEqual(application.build_pagination_items(1, 6), [1, 2, 3, 4])
        self.assertEqual(application.build_pagination_items(4, 6), [3, 4, 5, 6])
        self.assertEqual(application.build_pagination_items(5, 10), [4, 5, 6, 7])

        self.login()
        with patch.object(application, "fetch_readings", return_value=([], 240, None)):
            page = self.client.get("/data?page=5&per_page=25")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b'aria-current="page">5</span>', page.data)
            self.assertIn(b"Showing <strong>101\xe2\x80\x93125</strong> of <strong>240</strong>", page.data)

    def test_poppins_is_self_hosted(self):
        font_dir = Path(__file__).parent / "static" / "fonts"
        for name in ("Regular", "Medium", "SemiBold", "Bold"):
            self.assertGreater((font_dir / f"Poppins-{name}.ttf").stat().st_size, 100_000)

    def test_grouped_metric_cards_severity_and_trend_arrows(self):
        self.assertEqual(application.classify_sensor_reading("mq137_ppm", 754.6), "normal")
        self.assertEqual(application.classify_sensor_reading("mq137_ppm", 1500), "warning")
        self.assertEqual(application.classify_sensor_reading("mq137_ppm", 5100), "critical")
        self.assertEqual(application.classify_sensor_reading("mq4_ppm", 8), "normal")
        self.assertEqual(application.classify_sensor_reading("mq4_ppm", 14.3), "warning")
        self.assertEqual(application.classify_sensor_reading("mq4_ppm", 25), "critical")
        self.assertEqual(application.classify_sensor_reading("temperature_c", 31.2), "normal")
        self.assertEqual(application.classify_sensor_reading("temperature_c", 33.5), "warning")
        self.assertEqual(application.classify_sensor_reading("temperature_c", 36.0), "critical")
        self.assertEqual(application.classify_sensor_reading("humidity_perc", 63), "normal")
        self.assertEqual(application.classify_sensor_reading("humidity_perc", 45), "warning")
        self.assertEqual(application.classify_sensor_reading("humidity_perc", 85), "critical")
        self.assertEqual(application.classify_sensor_reading("co2_ppm", 1200), "normal")
        self.assertEqual(application.classify_sensor_reading("co2_ppm", 2689.3), "warning")
        self.assertEqual(application.classify_sensor_reading("co2_ppm", 3100), "critical")
        self.assertEqual(application.classify_sensor_reading("pm1_ugm3", 7.8), "normal")
        self.assertEqual(application.classify_sensor_reading("pm25_ugm3", 48.6), "warning")
        self.assertEqual(application.classify_sensor_reading("pm10_ugm3", 1111), "critical")
        self.assertEqual(application.reading_direction(14.3, 10.0), "up")
        self.assertEqual(application.reading_direction(63.0, 65.0), "down")
        self.assertEqual(application.reading_direction(31.2, 31.2), "flat")
        self.assertEqual(application.classify_reading_row(None), ("unavailable", "NO DATA"))
        self.assertEqual(
            application.classify_reading_row((datetime.now(timezone.utc), 31.0, 60.0, 500, 5, 20, 50, 5, 200)),
            ("normal", "NORMAL"),
        )
        self.assertEqual(
            application.classify_reading_row((datetime.now(timezone.utc), 31.0, 60.0, 500, 5, 20, 50, 15, 200)),
            ("warning", "WARNING"),
        )
        self.assertEqual(
            application.classify_reading_row((datetime.now(timezone.utc), 31.0, 60.0, 3200, 5, 20, 50, 5, 200)),
            ("critical", "CRITICAL"),
        )

        self.login()
        earliest = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)
        latest = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        latest_row = (
            latest,
            31.2, 63.0, 2689.3, 7.8, 48.6, 1111.0, 14.3, 754.6,
            30.0, 65.0, 2500.0, 7.8, 40.0, 900.0, 10.0, 800.0,
        )
        with patch.object(application, "get_db_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.side_effect = [(2, earliest, latest), latest_row, (0, 0, 0, 2) * 8]
            cursor.fetchall.return_value = []
            page = self.client.get("/dashboard")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Temperature and Humidity", page.data)
            self.assertIn(b"Particulate matter", page.data)
            self.assertIn(b"Carbon Dioxide", page.data)
            self.assertIn(b"metric-card metric-card-group is-normal", page.data)
            self.assertIn(b"metric-card metric-card-group is-critical", page.data)
            self.assertIn(b"metric-trend-arrow is-up", page.data)
            self.assertIn(b"metric-trend-arrow is-down", page.data)
            self.assertIn(b"metric-card-stretch", page.data)
            self.assertNotIn(b"Open graph", page.data)
            self.assertIn(b"metric-card-thresholds", page.data)

    def test_charts_extend_with_two_hour_dashed_forecast_and_api_charts(self):
        start = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 11, 11, 11, 0, tzinfo=timezone.utc)
        rows = [
            (start, None, "node-1", 400.0, 5.0, 31.0, 60.0, 8.0, 20.0, 50.0, 800.0),
            (end, None, "node-1", 450.0, 6.0, 31.5, 62.0, 9.0, 22.0, 55.0, 900.0),
        ]
        metrics = [dict(column=c, label=l, unit=u, value=rows[-1][3 + i], severity="normal")
                   for i, (c, l, u) in enumerate(application.SENSORS)]
        metrics_by_key = {m["column"]: m for m in metrics}
        forecast = {
            "status": "available",
            "device_id": "node-1",
            "input_cutoff_at": end.isoformat(),
            "target_at": (end + timedelta(hours=2)).isoformat(),
            "series": {
                col: [rows[-1][3 + i] + step * (10.0 if col == "mq137_ppm" else 0.1) for step in range(1, 121)]
                for i, (col, _, _) in enumerate(application.SENSORS)
            },
        }
        charts = application.build_sensor_charts(rows, start, end, "temperature_c", metrics_by_key, forecast=forecast)
        ammonia_chart = charts[0]
        ammonia_series = ammonia_chart["series_list"][0]
        self.assertEqual(len(ammonia_series["forecast_segments"]), 1)
        last_recorded_pt = f"{ammonia_series['points'][-1]['x']},{ammonia_series['points'][-1]['y']}"
        forecast_pts = ammonia_series["forecast_segments"][0].split()
        self.assertEqual(len(forecast_pts), 121)
        self.assertEqual(forecast_pts[0], last_recorded_pt)
        self.assertEqual(forecast_pts[-1].split(",")[0], "760.0")
        ticks = application.build_chart_ticks(start, end + timedelta(hours=2), has_forecast=True)
        self.assertEqual(len(ticks), 7)
        self.assertEqual(ticks[0][0], 60.0)
        self.assertEqual(ticks[-1][0], 760.0)

        self.login()
        fake_data = {
            "total": 2, "earliest": start, "latest": end, "metrics": metrics,
            "series": [], "start": start - timedelta(hours=23), "end": end,
            "recorded_series": rows, "recorded_start": start, "recorded_end": end,
        }
        with (
            patch.object(application, "fetch_dashboard_data", return_value=fake_data),
            patch.object(application, "get_db_connection"),
            patch.object(application, "fetch_latest_forecasts", return_value=[forecast]),
        ):
            response = self.client.get("/api/charts")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["configured"])
        self.assertTrue(payload["has_forecast"])
        self.assertEqual(len(payload["ticks"]), 7)
        self.assertEqual(len(payload["charts"]), 5)
        self.assertTrue(all(chart["series_list"][0]["forecast_segments"] for chart in payload["charts"]))

        charts_js = (Path(__file__).parent / "static" / "charts.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", charts_js)
        self.assertIn("createElementNS", charts_js)
        self.assertIn("AbortController", charts_js)
        self.assertIn("trend-axis-tick", charts_js)
        self.assertIn("trend-axis-unit", charts_js)

    def test_chart_axes_zoom_to_data_and_match_reference_layout(self):
        start = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 11, 11, 11, 0, tzinfo=timezone.utc)
        rows = [
            (start, None, "node-1", 30.8, 59.5, 1650.0, 4.5, 18.0, 42.0, 3.9, 880.0),
            (start + timedelta(minutes=30), None, "node-1", 31.3, 60.3, 2420.0, 6.0, 22.0, 50.0, 11.4, 945.0),
            (end, None, "node-1", 31.8, 61.5, 3172.0, 7.2, 26.0, 58.0, 19.9, 1016.0),
        ]
        metrics = [dict(column=c, label=l, unit=u, value=rows[-1][3 + i], severity="normal")
                   for i, (c, l, u) in enumerate(application.SENSORS)]
        metrics_by_key = {m["column"]: m for m in metrics}
        charts = application.build_sensor_charts(rows, start, end, "temperature_c", metrics_by_key)
        by_id = {c["chart_id"]: c for c in charts}

        # Y-axis ticks span bottom (232) to top (24) and zoom around actual values instead of full scale.
        ammonia = by_id["chart-mq137_ppm"]
        self.assertEqual(ammonia["y_ticks"][0][0], application.CHART_PLOT_BOTTOM)
        self.assertEqual(ammonia["y_ticks"][-1][0], application.CHART_PLOT_TOP)
        self.assertGreater(int(ammonia["y_ticks"][0][1].replace(",", "")), 750)
        self.assertLess(int(ammonia["y_ticks"][-1][1].replace(",", "")), 1200)

        methane = by_id["chart-mq4_ppm"]
        self.assertGreater(float(methane["y_ticks"][0][1]), 0.5)
        self.assertLess(float(methane["y_ticks"][-1][1]), 24.0)
        self.assertEqual(len({round(float(val), 1) for _, val in methane["y_ticks"]}), len(methane["y_ticks"]))

        co2 = by_id["chart-co2_ppm"]
        self.assertGreater(int(co2["y_ticks"][0][1].replace(",", "")), 1000)

        pm = by_id["chart-pm1_ugm3"]
        self.assertLess(int(pm["y_ticks"][-1][1].replace(",", "")), 400)

        # Flat series use minimum span rather than collapsing to zero height.
        flat_rows = [
            (start, None, "node-1", 31.0, 60.0, 500.0, 5.0, 15.0, 30.0, 4.0, 200.0),
            (end, None, "node-1", 31.0, 60.0, 500.0, 5.0, 15.0, 30.0, 4.0, 200.0),
        ]
        flat_charts = application.build_sensor_charts(flat_rows, start, end, "temperature_c", metrics_by_key)
        flat_co2_ticks = flat_charts[4]["y_ticks"]
        self.assertGreaterEqual(len(flat_co2_ticks), 3)
        self.assertNotEqual(flat_co2_ticks[0][1], flat_co2_ticks[-1][1])

        self.login()
        fake_data = {
            "total": 3, "earliest": start, "latest": end, "metrics": metrics,
            "series": [], "start": start - timedelta(hours=23), "end": end,
            "recorded_series": rows, "recorded_start": start, "recorded_end": end,
        }
        with patch.object(application, "fetch_dashboard_data", return_value=fake_data):
            page = self.client.get("/dashboard")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'viewBox="-18 14 798 252"', page.data)
        self.assertIn(b"trend-axis-tick", page.data)
        self.assertIn(b"trend-axis-unit", page.data)
        self.assertIn(b'transform="rotate(-90 -4 128)"', page.data)


if __name__ == "__main__":
    unittest.main()
