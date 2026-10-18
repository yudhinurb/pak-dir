import tempfile
import unittest
from pathlib import Path
from orchestrator.state_db import StateDB
from orchestrator.watchdog import SentinelWatchdog


class TestSentinelWatchdog(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_watchdog_"))
        self.db_path = self.temp_dir / "test_state.db"
        self.state_db = StateDB(self.db_path)
        self.watchdog = SentinelWatchdog(
            state_db=self.state_db,
            max_ram_percent=70.0,
            max_swap_percent=50.0,
            max_disk_percent=80.0,
            monitored_services=["caddy", "fake-service"],
            alert_cooldown_seconds=10.0,
        )

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cooldown_logic(self) -> None:
        key = "MEMORY_SPIKE"
        self.assertFalse(self.watchdog.is_in_cooldown(key))
        self.watchdog.mark_alert_sent(key)
        self.assertTrue(self.watchdog.is_in_cooldown(key))

    def test_memory_alert_generation(self) -> None:
        # Override get_memory_metrics to simulate spike
        self.watchdog.get_memory_metrics = lambda: {
            "ram_percent": 88.5,
            "ram_used_mb": 1750,
            "ram_total_mb": 2000,
            "swap_percent": 15.0,
            "swap_used_mb": 150,
            "swap_total_mb": 1000,
        }
        alert = self.watchdog.check_memory()
        self.assertIsNotNone(alert)
        self.assertEqual(alert["type"], "MEMORY_SPIKE")
        self.assertEqual(alert["severity"], "WARNING")
        self.assertIn("88.5% RAM", alert["title"])

    def test_disk_alert_generation(self) -> None:
        # Simulasikan disk usage tinggi
        self.watchdog.max_disk_percent = 0.01  # Rendahkan batas agar pasti kena
        alert = self.watchdog.check_disk()
        self.assertIsNotNone(alert)
        self.assertEqual(alert["type"], "DISK_FULL")

    def test_inspect_system_records_incidents_in_db(self) -> None:
        self.watchdog.get_memory_metrics = lambda: {
            "ram_percent": 95.0,
            "ram_used_mb": 1900,
            "ram_total_mb": 2000,
            "swap_percent": 70.0,
            "swap_used_mb": 700,
            "swap_total_mb": 1000,
        }
        alerts = self.watchdog.inspect_system()
        self.assertTrue(len(alerts) > 0)
        open_incidents = self.state_db.get_open_incidents()
        self.assertEqual(len(open_incidents), len(alerts))
        self.assertEqual(open_incidents[0]["status"], "OPEN")


if __name__ == "__main__":
    unittest.main()
