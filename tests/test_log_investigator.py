import tempfile
import unittest
from pathlib import Path
from typing import Tuple

from orchestrator.llm_router import FreeLLMRouter
from orchestrator.log_investigator import LogInvestigator
from orchestrator.state_db import StateDB


class TestLogInvestigator(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_log_inv_"))
        self.db_path = self.temp_dir / "test_state.db"
        self.state_db = StateDB(self.db_path)

        def mock_runner(agent_name: str, model: str, prompt: str) -> Tuple[int, str]:
            return 0, f"[MOCK RCA for {agent_name}]: Ditemukan OutOfMemoryError pada modul cache."

        self.router = FreeLLMRouter(state_db=self.state_db, runner=mock_runner)
        self.investigator = LogInvestigator(router=self.router, state_db=self.state_db)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_investigate_empty_log(self) -> None:
        res = self.investigator.investigate_log_text("", "Empty Log")
        self.assertIn("kosong", res["analysis"])

    def test_investigate_log_text(self) -> None:
        dummy_log = (
            "2026-10-18T04:12:00Z ERROR [api-backend] OutOfMemoryError: Java heap space\n"
            "at com.pakdir.CacheManager.init(CacheManager.java:42)"
        )
        res = self.investigator.investigate_log_text(dummy_log, "api-error.log")
        self.assertEqual(res["source"], "api-error.log")
        self.assertIn("OutOfMemoryError", res["analysis"])

    def test_investigate_service(self) -> None:
        res = self.investigator.investigate_service("caddy", lines=20)
        self.assertEqual(res["service"], "caddy")
        self.assertIn("MOCK RCA", res["analysis"])


if __name__ == "__main__":
    unittest.main()
