import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Tuple

from orchestrator.agent_factory import AgentFactory
from orchestrator.code_extractor import FORBIDDEN_TARGETS, extract_code_blocks, write_extracted_code_files
from orchestrator.llm_router import FreeLLMRouter
from orchestrator.state_db import StateDB
from orchestrator.supervisor import EMSupervisor


class TestSupervisorResumeAndWorkspace(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_root = Path(tempfile.mkdtemp(prefix="pakdir_test_root_"))
        self.projects_dir = Path(tempfile.mkdtemp(prefix="pakdir_test_projects_"))
        self.state_db = StateDB(self.temp_root / "data" / "test_state.db")
        self.factory = AgentFactory(root_dir=self.temp_root, state_db=self.state_db)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_root, ignore_errors=True)
        shutil.rmtree(self.projects_dir, ignore_errors=True)

    def test_workspace_isolation_and_target_directory_resolution(self) -> None:
        # 1. Resolusi dengan projects_dir eksplisit
        supervisor = EMSupervisor(
            root_dir=self.temp_root,
            state_db=self.state_db,
            projects_dir=self.projects_dir,
        )
        resolved = supervisor._resolve_task_project_dir("Buatkan website landing page", "landing-page")
        self.assertEqual(resolved, (self.projects_dir / "landing-page").resolve())

        # 2. Resolusi dengan custom target di instruksi pengguna
        custom_target = str(self.projects_dir / "custom_app").replace("\\", "/")
        resolved_custom = supervisor._resolve_task_project_dir(
            f"Buatkan web portfolio [target: {custom_target}] dengan tailwind",
            "portfolio",
        )
        self.assertEqual(resolved_custom, Path(custom_target).resolve())

        # 3. Proteksi berkas internal pak-dir oleh FORBIDDEN_TARGETS
        self.assertIn("orchestrator", FORBIDDEN_TARGETS)
        self.assertIn("opencode.json", FORBIDDEN_TARGETS)
        self.assertIn(".venv", FORBIDDEN_TARGETS)

        malicious_blocks = [
            {"path": "orchestrator/evil.py", "content": "# evil"},
            {"path": "opencode.json", "content": "{}"},
            {"path": "src/app.py", "content": "print('valid project code')"},
        ]
        target_dir = self.projects_dir / "test_protect"
        written = write_extracted_code_files(malicious_blocks, target_dir)
        written_names = [f.name for f in written]
        self.assertIn("app.py", written_names)
        self.assertNotIn("evil.py", written_names)
        self.assertFalse((target_dir / "orchestrator").exists())

    def test_resume_task_after_qa_failure(self) -> None:
        call_count = {"count": 0}

        def fake_runner(agent_name: str, model: str, prompt: str) -> Tuple[int, str]:
            if agent_name == "dev":
                return 0, (
                    "### File: main.py\n"
                    "```python\n"
                    "def run():\n"
                    "    return 'repaired'\n"
                    "```\n"
                )
            if agent_name == "ops":
                call_count["count"] += 1
                return 0, "Semua unit test 100% lulus setelah perbaikan."
            return 0, f"[{agent_name}] output"

        router = FreeLLMRouter(state_db=self.state_db, runner=fake_runner)
        target_proj = self.projects_dir / "calc_proj"

        supervisor = EMSupervisor(
            root_dir=self.temp_root,
            state_db=self.state_db,
            router=router,
            factory=self.factory,
            projects_dir=self.projects_dir,
            qa_runner=lambda root, files: (0, "All tests passed"),
        )

        # Buat task yang awalnya berhenti (misal gagal QA / butuh intervensi CTO)
        task_id = self.state_db.create_task(
            title="Kalkulator API",
            instruction="Buatkan kalkulator sederhana",
            branch_name="feat/calc",
            project_dir=str(target_proj),
        )
        self.state_db.update_task(
            task_id,
            status="NEEDS_CTO_INTERVENTION",
            current_stage="QA_FAILED_MAX_RETRIES",
            ops_output="Error QA: Syntax error on line 10",
        )

        progress_msgs = []
        resumed = supervisor.resume_task(
            task_id=task_id,
            additional_instruction="Gunakan format python 3 yang valid dan perbaiki sintaks.",
            progress_callback=progress_msgs.append,
        )

        self.assertEqual(resumed["status"], "WAITING_CTO_APPROVAL")
        self.assertEqual(resumed["current_stage"], "CTO_GATE")
        self.assertTrue((target_proj / "main.py").exists(), "Berkas main.py harus terbuat di direktori proyek target")
        self.assertIn("repaired", (target_proj / "main.py").read_text(encoding="utf-8"))
        self.assertTrue(any("Melanjutkan pengerjaan Task" in m for m in progress_msgs))

    def test_resume_task_after_rejection(self) -> None:
        def fake_runner(agent_name: str, model: str, prompt: str) -> Tuple[int, str]:
            if agent_name == "dev":
                return 0, "### File: index.html\n```html\n<h1>Web Revisi CTO</h1>\n```"
            return 0, "PASS: QA valid"

        router = FreeLLMRouter(state_db=self.state_db, runner=fake_runner)
        target_web = self.projects_dir / "web_site"

        supervisor = EMSupervisor(
            root_dir=self.temp_root,
            state_db=self.state_db,
            router=router,
            factory=self.factory,
            projects_dir=self.projects_dir,
            qa_runner=lambda root, files: (0, "OK"),
        )

        task_id = self.state_db.create_task(
            title="Landing Page",
            instruction="Buatkan landing page",
            branch_name="feat/landing",
            project_dir=str(target_web),
        )
        # CTO menolak
        supervisor.reject_task(task_id, "Tolong ganti judulnya jadi Web Revisi CTO")
        task_before = self.state_db.get_task(task_id)
        self.assertEqual(task_before["status"], "REJECTED_BY_CTO")

        # CTO memicu kembali dengan resume_task
        resumed = supervisor.resume_task(task_id, "Lakukan revisi segera")
        self.assertEqual(resumed["status"], "WAITING_CTO_APPROVAL")
        self.assertTrue((target_web / "index.html").exists())
        self.assertIn("Web Revisi CTO", (target_web / "index.html").read_text(encoding="utf-8"))
