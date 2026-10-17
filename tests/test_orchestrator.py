import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Tuple

from orchestrator.agent_factory import AgentFactory
from orchestrator.discord_bot import CTOCommandHandler, get_server_health_report
from orchestrator.llm_router import FreeLLMRouter
from orchestrator.state_db import StateDB
from orchestrator.supervisor import EMSupervisor


class TestAITechCompanyOrchestrator(unittest.TestCase):
    """
    Suite pengujian otomatis end-to-end untuk memverifikasi:
    1. Keabsahan opencode.json & 5 agen inti di workspace utama.
    2. Dynamic Agent Factory (hire_agent, specialize_agent, retire_agent, & org-chart regeneration).
    3. Multi-Provider Free LLM Router (rotasi otomatis saat HTTP 429 rate-limit).
    4. EM-Agent Supervisor (rekrutmen spesialis otomatis, Self-Healing Loop saat QA gagal, & Approval Gate CTO).
    """

    def setUp(self) -> None:
        self.project_root = Path(__file__).resolve().parent.parent
        self.temp_dir = Path(tempfile.mkdtemp(prefix="ai_company_test_"))

        # Salin konfigurasi inti ke sandbox test agar tidak mengotori file asli
        shutil.copy2(self.project_root / "opencode.json", self.temp_dir / "opencode.json")
        shutil.copytree(
            self.project_root / ".opencode",
            self.temp_dir / ".opencode",
        )
        (self.temp_dir / "docs" / "architecture").mkdir(parents=True, exist_ok=True)

        self.state_db = StateDB(self.temp_dir / "data" / "test_state.db")
        self.factory = AgentFactory(root_dir=self.temp_dir, state_db=self.state_db)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_workspace_opencode_config_and_core_agents_valid(self) -> None:
        cfg_path = self.project_root / "opencode.json"
        self.assertTrue(cfg_path.exists(), "opencode.json harus ada di root proyek")
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

        self.assertFalse(cfg.get("lsp", True), "LSP berat harus dimatikan (lsp: false) untuk efisiensi resident memory")
        for core_agent in ("em", "pm", "architect", "dev", "ops"):
            self.assertIn(core_agent, cfg["agent"], f"Agent inti @{core_agent} harus terdaftar")
            prompt_path = self.project_root / ".opencode" / "agent" / f"{core_agent}.md"
            self.assertTrue(prompt_path.exists(), f"File SOP {prompt_path} harus ada")

    def test_02_dynamic_agent_factory_hire_specialize_and_retire(self) -> None:
        # 1. Hire agent baru: security-auditor
        hired = self.factory.hire_agent(
            name="security-auditor",
            title="Security Auditor AI",
            description="Mengaudit celah keamanan kode",
            sop_rules="Periksa SQLi, XSS, dan JWT secret.",
            allowed_tools=["bash"],
        )
        self.assertEqual(hired["mode"], "subagent")
        self.assertTrue((self.temp_dir / ".opencode" / "agent" / "security-auditor.md").exists())

        # Pastikan org-chart.md ter-generate dan memuat @security-auditor
        org_chart = (self.temp_dir / "docs" / "architecture" / "org-chart.md").read_text(encoding="utf-8")
        self.assertIn("@security-auditor", org_chart)

        # 2. Specialize agent yang sudah ada (@dev)
        spec_res = self.factory.specialize_agent(
            name="dev",
            additional_focus="Wajib menggunakan Go Fiber dan SQLite WAL mode.",
        )
        self.assertEqual(spec_res["name"], "dev")
        dev_md = (self.temp_dir / ".opencode" / "agent" / "dev.md").read_text(encoding="utf-8")
        self.assertIn("Go Fiber dan SQLite WAL mode", dev_md)

        # 3. Pastikan agent inti dilindungi dari retire_agent
        with self.assertRaises(PermissionError):
            self.factory.retire_agent("em")

        # 4. Retire agent spesialis sementara
        self.factory.retire_agent("security-auditor")
        cfg_after = json.loads((self.temp_dir / "opencode.json").read_text(encoding="utf-8"))
        self.assertNotIn("security-auditor", cfg_after["agent"])
        self.assertTrue(
            (self.temp_dir / ".opencode" / "agent" / "archive" / "security-auditor.md").exists()
        )

    def test_03_free_llm_router_automatic_rate_limit_fallback(self) -> None:
        call_log = []

        def fake_runner(agent_name: str, model: str, prompt: str) -> Tuple[int, str]:
            call_log.append(model)
            if len(call_log) == 1:
                return 1, "Error 429: Rate limit exceeded on primary provider"
            return 0, f"OK from fallback {model}"

        router = FreeLLMRouter(state_db=self.state_db, runner=fake_runner)
        result = router.run_agent("architect", "Jelaskan desain database")

        self.assertEqual(len(call_log), 2, "Router harus otomatis pindah ke model kedua saat model pertama 429")
        self.assertIn("OK from fallback", result["output"])

    def test_04_supervisor_self_healing_loop_and_cto_approval_gate(self) -> None:
        ops_attempts = {"count": 0}

        def fake_runner(agent_name: str, model: str, prompt: str) -> Tuple[int, str]:
            if agent_name == "ops" and "APPROVAL" not in prompt:
                ops_attempts["count"] += 1
                if ops_attempts["count"] == 1:
                    return 0, "FAIL: Unit test test_payment_signature gagal di line 42"
                return 0, "PASS: Semua 12 unit test lulus, sisa RAM 1450 MB"
            return 0, f"[{agent_name}] selesai dikerjakan menggunakan {model}"

        router = FreeLLMRouter(state_db=self.state_db, runner=fake_runner)
        supervisor = EMSupervisor(
            root_dir=self.temp_dir,
            state_db=self.state_db,
            router=router,
            factory=self.factory,
            max_retries=3,
        )

        logs = []
        # Instruksi mengandung kata 'keamanan' & 'jwt' -> EM otomatis merekrut @security-auditor
        task = supervisor.execute_cto_mandate(
            "Bikin fitur autentikasi JWT dengan audit keamanan ketat",
            progress_callback=logs.append,
        )

        self.assertEqual(task["status"], "WAITING_CTO_APPROVAL")
        self.assertEqual(task["retry_count"], 1, "Harus tercatat 1x self-healing retry karena percobaan QA pertama gagal")

        # Pastikan @security-auditor otomatis direkrut oleh EM-Agent
        active_names = {a["name"] for a in self.factory.list_agents()}
        self.assertIn("security-auditor", active_names)

        # Verifikasi berkas fisik deliverable dibuat di disk
        prd_files = list((self.temp_dir / "docs" / "prd").glob("task_*.md"))
        self.assertTrue(len(prd_files) > 0, "Berkas PRD fisik harus terbuat di docs/prd/")
        arch_files = list((self.temp_dir / "docs" / "architecture" / "specs").glob("task_*.md"))
        self.assertTrue(len(arch_files) > 0, "Berkas arsitektur fisik harus terbuat di docs/architecture/specs/")
        deliv_files = list((self.temp_dir / "docs" / "deliverables").glob("task_*.md"))
        self.assertTrue(len(deliv_files) > 0, "Berkas implementasi fisik harus terbuat di docs/deliverables/")

        # Simulasikan CTO mengetik !approve <id>
        approved = supervisor.approve_task(task["id"], "LGTM dari CTO!")
        self.assertEqual(approved["status"], "APPROVED_AND_DEPLOYED")
        self.assertEqual(approved["current_stage"], "PRODUCTION")
        self.assertTrue(bool(approved.get("commit_hash")), "Harus tercatat hash commit deploy")
        self.assertTrue(len(approved.get("merged_files", [])) > 0, "Harus tercatat daftar file yang di-merge")

    def test_05_cto_command_handler_and_health_report(self) -> None:
        handler = CTOCommandHandler(root_dir=self.temp_dir)
        agents_text = handler.handle_agents_list()
        self.assertIn("@em", agents_text)
        self.assertIn("@architect", agents_text)

        health_text = get_server_health_report()
        self.assertIn("Status Telemetri & Kesehatan Node CTO", health_text)

    def test_06_system_context_and_message_chunking(self) -> None:
        from orchestrator.system_context import get_live_server_context, get_listening_ports
        from orchestrator.discord_bot import send_split_message
        import asyncio

        # 1. Cek snapshot telemetri
        ctx = get_live_server_context()
        self.assertIn("LIVE SERVER & HOST TELEMETRY", ctx)
        ports = get_listening_ports()
        self.assertTrue(len(ports) > 0)

        # 2. Cek pemecahan pesan panjang tanpa merusak markdown codeblock
        sent_messages = []

        class MockCtx:
            async def send(self, msg: str) -> None:
                sent_messages.append(msg)

        mock_ctx = MockCtx()
        long_content = "Ini baris pembuka.\n```python\n" + ("x = 1\n" * 400) + "```\nIni baris penutup."
        asyncio.run(send_split_message(mock_ctx, long_content, max_chunk_size=1000))

        self.assertGreater(len(sent_messages), 1, "Pesan panjang harus dipecah menjadi beberapa chunk")
        # Pastikan tidak ada blok kode yang menggantung tanpa penutup
        for m in sent_messages:
            self.assertEqual(m.count("```") % 2, 0, "Setiap potongan pesan harus memiliki penutup triple backtick genap")

    def test_07_observability_status_taskinfo_and_activity(self) -> None:
        handler = CTOCommandHandler(root_dir=self.temp_dir, state_db=self.state_db)
        # 1. Cek !status saat idle
        status_empty = handler.handle_status()
        self.assertIn("Semua agen saat ini idle", status_empty)

        # 2. Buat task dan event untuk simulasi
        t_id = self.state_db.create_task("Test Observability", "Instruksi Observabilitas", "feat/test-obs")
        self.state_db.update_task(
            t_id,
            status="WAITING_CTO_APPROVAL",
            current_stage="CTO_GATE",
            pm_output="## PRD Test: Fitur Observabilitas",
            arch_output="## Desain Arsitektur: Modul Status Real-Time",
            dev_output="## Implementasi: Handler Status & Activity",
            ops_output="PASS: 10/10 test sukses",
        )
        self.state_db.log_agent_event("STAGE_FINISH", "dev", f"Task #{t_id}: Kode selesai")

        # 3. Cek !status saat ada task waiting
        status_with_task = handler.handle_status()
        self.assertIn(f"Task #{t_id}", status_with_task)
        self.assertIn("WAITING_CTO_APPROVAL", status_with_task)

        # 4. Cek !taskinfo <id> memuat semua deliverable
        taskinfo_text = handler.handle_taskinfo(t_id)
        self.assertIn("BUKTI HASIL KERJA RESMI", taskinfo_text)
        self.assertIn("PRD Test: Fitur Observabilitas", taskinfo_text)
        self.assertIn("Desain Arsitektur: Modul Status Real-Time", taskinfo_text)
        self.assertIn("Implementasi: Handler Status & Activity", taskinfo_text)
        self.assertIn("10/10 test sukses", taskinfo_text)

        # 5. Cek !activity
        activity_text = handler.handle_activity()
        self.assertIn("LOG AKTIVITAS AGEN", activity_text)
        self.assertIn("@dev", activity_text)

    def test_08_real_git_workflow_on_git_repository(self) -> None:
        import subprocess

        # Inisialisasi repo git di self.temp_dir
        subprocess.run(["git", "init"], cwd=str(self.temp_dir), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(self.temp_dir), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(self.temp_dir), capture_output=True, check=True)

        # Buat initial commit di main
        (self.temp_dir / "README.md").write_text("# Initial", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=str(self.temp_dir), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(self.temp_dir), capture_output=True, check=True)
        subprocess.run(["git", "branch", "-M", "main"], cwd=str(self.temp_dir), capture_output=True, check=True)

        def fake_runner(agent_name: str, model: str, prompt: str) -> Tuple[int, str]:
            return 0, f"[{agent_name}] output untuk test git workflow"

        router = FreeLLMRouter(state_db=self.state_db, runner=fake_runner)
        supervisor = EMSupervisor(
            root_dir=self.temp_dir,
            state_db=self.state_db,
            router=router,
            factory=self.factory,
            max_retries=1,
        )

        self.assertTrue(supervisor._is_git_repo(), "Harus mendeteksi git repo aktif")

        # Jalankan mandate
        task = supervisor.execute_cto_mandate("Fitur git branch otomatis")
        self.assertEqual(task["status"], "WAITING_CTO_APPROVAL")

        # Cek bahwa branch dibuat di git
        branches_out = subprocess.run(["git", "branch"], cwd=str(self.temp_dir), capture_output=True, text=True).stdout
        self.assertIn("feat/", branches_out)

        # Approve task
        approved = supervisor.approve_task(task["id"], "Approve merge real git")
        self.assertEqual(approved["status"], "APPROVED_AND_DEPLOYED")
        commit_hash = approved.get("commit_hash", "")
        self.assertTrue(len(commit_hash) >= 4, f"Commit hash harus valid, got {commit_hash}")

        # Cek log git di main
        log_out = subprocess.run(["git", "log", "-1", "--oneline"], cwd=str(self.temp_dir), capture_output=True, text=True).stdout
        self.assertIn("deploy: merge task #", log_out)


if __name__ == "__main__":
    unittest.main()

