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

        # Simulasikan CTO mengetik !approve <id>
        approved = supervisor.approve_task(task["id"], "LGTM dari CTO!")
        self.assertEqual(approved["status"], "APPROVED_AND_DEPLOYED")
        self.assertEqual(approved["current_stage"], "PRODUCTION")

    def test_05_cto_command_handler_and_health_report(self) -> None:
        handler = CTOCommandHandler(root_dir=self.temp_dir)
        agents_text = handler.handle_agents_list()
        self.assertIn("@em", agents_text)
        self.assertIn("@architect", agents_text)

        health_text = get_server_health_report()
        self.assertIn("Status Telemetri & Kesehatan Node CTO", health_text)


if __name__ == "__main__":
    unittest.main()
