import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Tuple

from orchestrator.llm_router import FreeLLMRouter
from orchestrator.patch_gatekeeper import PatchGatekeeper
from orchestrator.state_db import StateDB


class TestPatchGatekeeper(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_patch_gk_")).resolve()
        self.root_path = self.temp_dir
        self.db_path = self.root_path / "data" / "test_state.db"
        self.state_db = StateDB(self.db_path)

        # Setup dummy git repo
        subprocess.run(["git", "init"], cwd=str(self.root_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=str(self.root_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(self.root_path), capture_output=True)
        (self.root_path / "README.md").write_text("# Project", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=str(self.root_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=str(self.root_path), capture_output=True)
        subprocess.run(["git", "branch", "-M", "main"], cwd=str(self.root_path), capture_output=True)

        def mock_runner(agent_name: str, model: str, prompt: str) -> Tuple[int, str]:
            return 0, (
                "Berikut perbaikan konfigurasi:\n"
                "### File: config.json\n"
                "```json\n"
                '{"timeout": 60, "workers": 4}\n'
                "```\n"
            )

        self.router = FreeLLMRouter(state_db=self.state_db, runner=mock_runner)
        self.gatekeeper = PatchGatekeeper(
            root_dir=self.root_path,
            state_db=self.state_db,
            router=self.router,
        )

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir.name, ignore_errors=True)

    def test_hotfix_lifecycle_and_approval(self) -> None:
        # 1. Prepare Hotfix (terisolasi di branch hotfix/...)
        task = self.gatekeeper.prepare_hotfix("Ubah timeout ke 60 detik")
        task_id = task["id"]
        self.assertEqual(task["status"], "WAITING_CTO_APPROVAL")
        self.assertTrue(task["branch_name"].startswith("hotfix/"))

        # 2. Approve and deploy (merge ke main)
        approved = self.gatekeeper.approve_and_deploy(task_id, note="Approved by CTO")
        self.assertEqual(approved["status"], "APPROVED_AND_DEPLOYED")
        self.assertNotEqual(approved["commit_hash"], "")
        # File config.json sekarang sudah ter-merge ke main
        self.assertTrue((self.root_path / "config.json").exists())

    def test_hotfix_rejection(self) -> None:
        task = self.gatekeeper.prepare_hotfix("Perbaiki crash memori")
        task_id = task["id"]
        rejected = self.gatekeeper.reject_task(task_id, reason="Konfigurasi kurang tepat")
        self.assertEqual(rejected["status"], "REJECTED_BY_CTO")
        self.assertEqual(rejected["cto_feedback"], "Konfigurasi kurang tepat")


if __name__ == "__main__":
    unittest.main()
