import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from orchestrator.agent_factory import AgentFactory
from orchestrator.llm_router import FreeLLMRouter
from orchestrator.state_db import StateDB


# Aturan deteksi otomatis kebutuhan agen spesialis baru oleh EM-Agent
SPECIALIST_TRIGGERS: List[Dict[str, Any]] = [
    {
        "keywords": ("security", "keamanan", "audit", "owasp", "injection", "jwt", "auth", "kripto", "crypto"),
        "name": "security-auditor",
        "title": "Application Security Auditor",
        "description": "Mengaudit celah keamanan OWASP, autentikasi, dan validasi input",
        "sop": (
            "1. Periksa setiap endpoint atau modul baru dari celah SQL Injection, XSS, SSRF, dan Broken Auth.\n"
            "2. Pastikan tidak ada secret/token yang di-hardcode di dalam source code.\n"
            "3. Berikan daftar rekomendasi perbaikan ke @dev sebelum masuk ke tahap produksi."
        ),
        "tools": ["bash"],
    },
    {
        "keywords": ("sql", "database", "query", "indeks", "index", "migrasi", "erd", "sqlite"),
        "name": "db-optimizer",
        "title": "Database & Query Optimizer",
        "description": "Mengoptimalkan skema database dan performa query hemat memori host",
        "sop": (
            "1. Pastikan semua query memiliki indeks yang tepat dan tidak memicu full-table scan besar di RAM.\n"
            "2. Gunakan SQLite WAL mode dan batasi ukuran cache di memori.\n"
            "3. Sinkronkan perubahan tabel dengan docs/architecture/database-erd.md."
        ),
        "tools": ["write", "edit", "bash"],
    },
    {
        "keywords": ("ui", "ux", "frontend", "dashboard", "tampilan", "css", "tailwind", "html"),
        "name": "frontend-specialist",
        "title": "Frontend & UX Specialist",
        "description": "Merancang antarmuka web yang ringan, responsif, dan cepat",
        "sop": (
            "1. Bangun antarmuka yang bersih, responsif di HP maupun desktop, dan minim bundle JS raksasa.\n"
            "2. Hindari proses build frontend yang membebani memori host secara berlebihan."
        ),
        "tools": ["write", "edit", "bash"],
    },
]


class EMSupervisor:
    """
    Mesin Supervisor EM-Agent (Engineering Manager).
    Mengatur:
    1. Evaluasi kesiapan tim (otomatis `hire_agent` / `specialize_agent` jika dibutuhkan).
    2. Eksekusi tahapan: PM -> Architect -> Dev (+ Spesialis) -> Ops.
    3. Self-Healing Loop (maksimal 3x iterasi otomatis jika Ops melaporkan kegagalan test).
    4. Pendaftaran ke Gerbang Persetujuan CTO (`WAITING_CTO_APPROVAL`).
    """

    def __init__(
        self,
        root_dir: str | Path = ".",
        state_db: Optional[StateDB] = None,
        router: Optional[FreeLLMRouter] = None,
        factory: Optional[AgentFactory] = None,
        max_retries: int = 3,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.state_db = state_db or StateDB(self.root_dir / "data" / "company_state.db")
        self.router = router or FreeLLMRouter(state_db=self.state_db)
        self.factory = factory or AgentFactory(root_dir=self.root_dir, state_db=self.state_db)
        self.max_retries = max_retries

    @staticmethod
    def _slugify_branch(title: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return f"feat/{slug[:32] or 'update'}"

    def evaluate_and_prepare_team(self, instruction: str) -> List[str]:
        """
        EM-Agent memeriksa instruksi CTO dan secara dinamis merekrut agen spesialis baru
        jika keahlian tersebut belum ada di daftar agen aktif.
        """
        lowered = instruction.lower()
        existing_names = {a["name"] for a in self.factory.list_agents()}
        recruited_or_assigned: List[str] = []

        for rule in SPECIALIST_TRIGGERS:
            if any(kw in lowered for kw in rule["keywords"]):
                spec_name = rule["name"]
                if spec_name not in existing_names:
                    self.factory.hire_agent(
                        name=spec_name,
                        title=rule["title"],
                        description=rule["description"],
                        sop_rules=rule["sop"],
                        allowed_tools=rule["tools"],
                    )
                    existing_names.add(spec_name)
                recruited_or_assigned.append(spec_name)

        return recruited_or_assigned

    def execute_cto_mandate(
        self,
        instruction: str,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """
        Menjalankan alur kerja end-to-end di bawah pengawasan EM-Agent hingga siap di-approve CTO.
        """
        def notify(msg: str) -> None:
            if progress_callback:
                progress_callback(msg)

        short_title = instruction.strip().split("\n")[0][:60]
        branch_name = self._slugify_branch(short_title)
        task_id = self.state_db.create_task(
            title=short_title,
            instruction=instruction,
            branch_name=branch_name,
        )

        notify(f"👔 **[EM-Agent]** Menerima Task #{task_id}: *{short_title}* (Branch: `{branch_name}`)")

        # Tahap 0: Evaluasi Tim & Dynamic Agent Factory
        specialists = self.evaluate_and_prepare_team(instruction)
        if specialists:
            spec_tags = ", ".join(f"`@{s}`" for s in specialists)
            notify(f"🏭 **[Agent Factory]** Tim spesialis disiapkan/ditugaskan: {spec_tags}")

        # Tahap 1: Product Manager (PRD)
        self.state_db.update_task(task_id, current_stage="PM_PRD")
        pm_res = self.router.run_agent(
            "pm",
            f"Buat spesifikasi PRD ringkas dan kriteria penerimaan untuk: {instruction}",
        )
        notify(f"📋 **[@pm]** Selesai menyusun spesifikasi (Model: `{pm_res['model']}`).")

        # Tahap 2: System Architect (Desain & Update Diagram Portal)
        self.state_db.update_task(task_id, current_stage="ARCH_DESIGN")
        arch_res = self.router.run_agent(
            "architect",
            (
                f"Berdasarkan PRD berikut:\n{pm_res['output']}\n\n"
                f"Rancang arsitektur hemat sumber daya dan perbarui diagram di docs/architecture/ untuk: {instruction}"
            ),
        )
        notify(f"🏗️ **[@architect]** Selesai memperbarui desain & diagram arsitektur (Model: `{arch_res['model']}`).")

        # Tahap 3 & 4: Dev + Spesialis + Ops dengan Self-Healing Loop
        attempt = 0
        qa_passed = False
        last_ops_output = ""
        dev_output = ""

        while attempt < self.max_retries:
            attempt += 1
            self.state_db.update_task(
                task_id,
                current_stage=f"DEV_CODING_ATTEMPT_{attempt}",
                retry_count=attempt - 1,
            )

            fix_context = (
                f"\nPerbaiki error QA berikut dari percobaan sebelumnya:\n{last_ops_output}"
                if last_ops_output
                else ""
            )
            dev_res = self.router.run_agent(
                "dev",
                (
                    f"Implementasikan fitur di branch {branch_name} sesuai desain Architect:\n"
                    f"{arch_res['output']}{fix_context}"
                ),
            )
            dev_output = dev_res["output"]

            # Jalankan agen spesialis jika ada
            for spec_agent in specialists:
                self.router.run_agent(
                    spec_agent,
                    f"Tinjau dan optimalkan implementasi pada branch {branch_name}: {dev_output}",
                )

            # Jalankan QA & DevOps Verification
            self.state_db.update_task(task_id, current_stage=f"OPS_QA_ATTEMPT_{attempt}")
            ops_res = self.router.run_agent(
                "ops",
                f"Jalankan unit test dan periksa telemetri kesehatan sistem untuk branch {branch_name}.",
            )
            last_ops_output = ops_res["output"]

            if "FAIL" not in last_ops_output.upper() and "ERROR:" not in last_ops_output.upper():
                qa_passed = True
                break

            notify(
                f"⚠️ **[EM-Agent Self-Healing]** Percobaan #{attempt} menemukan kendala QA. "
                f"Menginstruksikan `@dev` memperbaiki otomatis..."
            )

        if not qa_passed:
            self.state_db.update_task(
                task_id,
                status="NEEDS_CTO_INTERVENTION",
                current_stage="QA_FAILED_MAX_RETRIES",
                summary=last_ops_output,
            )
            notify(
                f"❌ **[EM-Agent]** Task #{task_id} gagal lulus QA setelah {self.max_retries}x percobaan. "
                f"Butuh arahan CTO."
            )
            return self.state_db.get_task(task_id) or {}

        # Tahap 5: Masuk Gerbang Approval CTO
        summary_report = (
            f"Branch `{branch_name}` lulus QA (Percobaan ke-{attempt}).\n"
            f"- Spesialis terlibat: {', '.join(specialists) if specialists else 'Tim Inti'}\n"
            f"- Ringkasan Architect: {arch_res['output'][:180]}\n"
            f"- Ringkasan QA/Ops: {last_ops_output[:180]}"
        )
        self.state_db.update_task(
            task_id,
            status="WAITING_CTO_APPROVAL",
            current_stage="CTO_GATE",
            retry_count=attempt - 1,
            summary=summary_report,
        )
        notify(
            f"✅ **[EM-Agent]** Task #{task_id} siap! Ketik `!approve {task_id}` untuk Merge & Deploy, "
            f"atau `!reject {task_id} <alasan>` untuk revisi."
        )
        return self.state_db.get_task(task_id) or {}

    def approve_task(self, task_id: int, cto_note: str = "Approved by CTO") -> Dict[str, Any]:
        task = self.state_db.get_task(task_id)
        if not task:
            raise KeyError(f"Task #{task_id} tidak ditemukan.")
        if task["status"] != "WAITING_CTO_APPROVAL":
            raise ValueError(
                f"Task #{task_id} berstatus '{task['status']}', bukan 'WAITING_CTO_APPROVAL'."
            )

        ops_deploy = self.router.run_agent(
            "ops",
            f"CTO telah memberikan APPROVAL untuk Task #{task_id}. Merge branch {task['branch_name']} ke main dan jalankan smoke test.",
        )
        self.state_db.update_task(
            task_id,
            status="APPROVED_AND_DEPLOYED",
            current_stage="PRODUCTION",
            cto_feedback=cto_note,
            summary=f"{task['summary']}\n[DEPLOYED]: {ops_deploy['output']}",
        )
        return self.state_db.get_task(task_id) or {}

    def reject_task(self, task_id: int, reason: str) -> Dict[str, Any]:
        task = self.state_db.get_task(task_id)
        if not task:
            raise KeyError(f"Task #{task_id} tidak ditemukan.")

        self.state_db.update_task(
            task_id,
            status="REJECTED_BY_CTO",
            current_stage="REVISION_REQUESTED",
            cto_feedback=reason,
        )
        return self.state_db.get_task(task_id) or {}
