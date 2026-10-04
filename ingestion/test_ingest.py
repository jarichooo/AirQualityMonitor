import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import psycopg2
import ingest


class IngestionTests(unittest.TestCase):
    def test_timestamp_and_partial_reading(self):
        for timestamp in ("2026-10-01T08:00:00+08:00", "2026-10-01 08:00:00", "2026-10-01T00:00:00Z"):
            row = ingest.parse_reading(json.dumps({"ts": timestamp, "t": 30, "device_id": "node-1"}).encode())
            self.assertEqual(row[0], datetime(2026, 10, 1, tzinfo=timezone.utc))
            self.assertEqual(row[1:4], ("node-1", 30, None))
        self.assertEqual(ingest.parse_reading(b'{"t":30}')[0].tzinfo, timezone.utc)

    def test_invalid_readings(self):
        for raw in (b"bad JSON", b"[]", b"{}", b'{"t":true}', b'{"h":101}',
                    b'{"co2":-1}', b'{"t":NaN}', b'{"t":30,"ts":"bad"}',
                    b'{"t":30,"device_id":123}', b'{"t":30,"ts":123}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                ingest.parse_reading(raw)

    def test_raw_mq_and_all_particle_sizes(self):
        raw = b'{"pm1":8,"pm25":15,"pm10":22,"mq135_raw":1200,"mq137_raw":1600}'
        row = ingest.parse_reading(raw)
        self.assertEqual(row[2:], (None, None, None, 8, 15, 22, 1200, 1600))
        self.assertEqual(ingest.parse_reading(b'{"mq135":12,"mq137":32}')[8:], (12, 32))
        self.assertEqual(ingest.parse_reading(b'{"mq135_raw":0,"mq135":99}')[8], 0)
        for invalid in (b'{"pm1":-1}', b'{"pm10":NaN}', b'{"mq135_raw":true}', b'{"mq137_raw":"12"}'):
            with self.subTest(raw=invalid), self.assertRaises(ValueError):
                ingest.parse_reading(invalid)
        with patch.object(ingest.psycopg2, "connect") as connect:
            ingest.save_reading(row)
            query, values = connect.return_value.cursor.return_value.__enter__.return_value.execute.call_args.args
            self.assertIn("pm1_ugm3, pm25_ugm3, pm10_ugm3, mq135_raw, mq137_raw", query)
            self.assertEqual(query.count("%s"), len(values))

    def test_sensor_device_payload_and_failed_sensors(self):
        # A real device omits failed sensors instead of sending -1/-999 sentinels.
        payload = {"device_id": "esp32-AABBCCDDEEFF", "ts": "2026-10-04T14:00:00+08:00",
                   "t": 30.25, "h": 68.5, "co2": 420, "pm1": 8, "pm25": 15,
                   "pm10": 22, "mq135_raw": 1200.5, "mq137_raw": 1600.2}
        row = ingest.parse_reading(json.dumps(payload).encode())
        self.assertEqual(row[0], datetime(2026, 10, 4, 6, tzinfo=timezone.utc))
        self.assertEqual(row[2:], (30.25, 68.5, 420, 8, 15, 22, 1200.5, 1600.2))
        for key in ("t", "h", "co2", "pm1", "pm25", "pm10"):
            payload.pop(key)
        self.assertEqual(ingest.parse_reading(json.dumps(payload).encode())[2:],
                         (None, None, None, None, None, None, 1200.5, 1600.2))

    def test_database_failure_does_not_stop_next_reading(self):
        msg = SimpleNamespace(payload=b'{"t":30}')
        with patch.object(ingest, "save_reading", side_effect=[psycopg2.OperationalError("offline"), None]) as save:
            ingest.on_message(None, None, msg)
            ingest.on_message(None, None, msg)
            self.assertEqual(save.call_count, 2)
        with patch.object(ingest, "save_reading") as save:
            ingest.on_message(None, None, SimpleNamespace(payload=b'{"h":200}'))
            save.assert_not_called()

    def test_failed_transaction_closes_connection(self):
        row = ingest.parse_reading(b'{"t":30}')
        with patch.object(ingest.psycopg2, "connect") as connect:
            conn = connect.return_value
            cursor = conn.cursor.return_value.__enter__.return_value
            cursor.execute.side_effect = psycopg2.DataError("invalid insert")
            with self.assertRaises(psycopg2.DataError):
                ingest.save_reading(row)
            conn.__exit__.assert_called_once()
            self.assertEqual(conn.__exit__.call_args.args[0], psycopg2.DataError)
            conn.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
