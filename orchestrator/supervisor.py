import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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

    def _run_git(self, args: List[str]) -> Tuple[int, str]:
        """Menjalankan perintah git dengan git committer default agar tidak gagal saat config user kosong."""
        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"] = "PakDir AI"
        env["GIT_AUTHOR_EMAIL"] = "bot@pakdir.ai"
        env["GIT_COMMITTER_NAME"] = "PakDir AI"
        env["GIT_COMMITTER_EMAIL"] = "bot@pakdir.ai"
        try:
            proc = subprocess.run(
                ["git"] + args,
                cwd=str(self.root_dir),
                capture_output=True,
                text=True,
                env=env,
                timeout=20,
            )
            output = proc.stdout.strip() or proc.stderr.strip()
            return proc.returncode, output
        except Exception as exc:
            return 1, str(exc)

    def _is_git_repo(self) -> bool:
        code, out = self._run_git(["rev-parse", "--is-inside-work-tree"])
        return code == 0 and out.strip() == "true"

    def _get_current_branch(self) -> str:
        code, out = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        return out.strip() if code == 0 and out.strip() else "main"

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
        Menjalankan alur kerja end-to-end di bawah pengawasan EM-Agent hingga siap di-approve CTO:
        1. Menulis berkas fisik PRD, Arsitektur, & Deliverable Kode.
        2. Membuat branch Git terpisah dan meng-commit berkas deliverable.
        3. Mencatat setiap event tahapan agen ke database secara transparan.
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

        self.state_db.log_agent_event(
            "MANDATE_RECEIVED",
            "em",
            f"Menerima Task #{task_id}: {short_title} (Branch: {branch_name})",
        )
        notify(f"👔 **[EM-Agent]** Menerima Task #{task_id}: *{short_title}* (Branch: `{branch_name}`)")

        # Tahap 0: Evaluasi Tim & Dynamic Agent Factory
        specialists = self.evaluate_and_prepare_team(instruction)
        if specialists:
            spec_tags = ", ".join(f"`@{s}`" for s in specialists)
            notify(f"🏭 **[Agent Factory]** Tim spesialis disiapkan/ditugaskan: {spec_tags}")

        # Tahap 1: Product Manager (PRD)
        self.state_db.update_task(task_id, current_stage="PM_PRD")
        self.state_db.log_agent_event(
            "STAGE_START",
            "pm",
            f"Task #{task_id}: Mulai menyusun spesifikasi PRD dan kriteria penerimaan",
        )
        pm_res = self.router.run_agent(
            "pm",
            f"Buat spesifikasi PRD ringkas dan kriteria penerimaan untuk: {instruction}",
        )

        # Simpan deliverable fisik PRD
        slug = branch_name.replace("feat/", "")
        prd_dir = self.root_dir / "docs" / "prd"
        prd_dir.mkdir(parents=True, exist_ok=True)
        prd_file = prd_dir / f"task_{task_id}_{slug}.md"
        prd_content = (
            f"# PRD Task #{task_id}: {short_title}\n\n"
            f"- **Branch:** `{branch_name}`\n"
            f"- **Instruksi CTO:** {instruction}\n"
            f"- **Model Generator:** `{pm_res['model']}`\n\n"
            f"## Spesifikasi & Kriteria Penerimaan\n\n"
            f"{pm_res['output']}\n"
        )
        prd_file.write_text(prd_content, encoding="utf-8")
        self.state_db.update_task(task_id, pm_output=pm_res["output"])
        self.state_db.log_agent_event(
            "STAGE_FINISH",
            "pm",
            f"Task #{task_id}: PRD berhasil dibuat dan disimpan di docs/prd/{prd_file.name}",
        )
        notify(f"📋 **[@pm]** Selesai menyusun spesifikasi (Model: `{pm_res['model']}`). Berkas: `docs/prd/{prd_file.name}`")

        # Tahap 2: System Architect (Desain & Update Diagram Portal)
        self.state_db.update_task(task_id, current_stage="ARCH_DESIGN")
        self.state_db.log_agent_event(
            "STAGE_START",
            "architect",
            f"Task #{task_id}: Merancang arsitektur sistem dan spesifikasi teknis",
        )
        arch_res = self.router.run_agent(
            "architect",
            (
                f"Berdasarkan PRD berikut:\n{pm_res['output']}\n\n"
                f"Rancang arsitektur hemat sumber daya dan perbarui diagram di docs/architecture/ untuk: {instruction}"
            ),
        )

        # Simpan deliverable fisik Desain Arsitektur
        arch_dir = self.root_dir / "docs" / "architecture" / "specs"
        arch_dir.mkdir(parents=True, exist_ok=True)
        arch_file = arch_dir / f"task_{task_id}_design.md"
        arch_content = (
            f"# Desain Arsitektur Task #{task_id}: {short_title}\n\n"
            f"- **Branch:** `{branch_name}`\n"
            f"- **Model Generator:** `{arch_res['model']}`\n\n"
            f"## Rancangan Arsitektur & Diagram Komponen\n\n"
            f"{arch_res['output']}\n"
        )
        arch_file.write_text(arch_content, encoding="utf-8")
        self.state_db.update_task(task_id, arch_output=arch_res["output"])
        self.state_db.log_agent_event(
            "STAGE_FINISH",
            "architect",
            f"Task #{task_id}: Desain arsitektur disimpan di docs/architecture/specs/{arch_file.name}",
        )
        notify(f"🏗️ **[@architect]** Selesai merancang arsitektur (Model: `{arch_res['model']}`). Berkas: `docs/architecture/specs/{arch_file.name}`")

        # Tahap 3 & 4: Dev + Spesialis + Ops dengan Self-Healing Loop
        attempt = 0
        qa_passed = False
        last_ops_output = ""
        dev_output = ""

        deliv_dir = self.root_dir / "docs" / "deliverables"
        deliv_dir.mkdir(parents=True, exist_ok=True)
        deliv_file = deliv_dir / f"task_{task_id}_implementation.md"

        while attempt < self.max_retries:
            attempt += 1
            self.state_db.update_task(
                task_id,
                current_stage=f"DEV_CODING_ATTEMPT_{attempt}",
                retry_count=attempt - 1,
            )
            self.state_db.log_agent_event(
                "STAGE_START",
                "dev",
                f"Task #{task_id}: Menulis kode implementasi (Percobaan #{attempt})",
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

            # Simpan deliverable implementasi kode
            deliv_content = (
                f"# Implementasi Deliverable Task #{task_id}: {short_title}\n\n"
                f"- **Branch:** `{branch_name}`\n"
                f"- **Attempt:** {attempt}\n"
                f"- **Model Generator:** `{dev_res['model']}`\n\n"
                f"## Kode & Ringkasan Perubahan\n\n"
                f"{dev_output}\n"
            )
            deliv_file.write_text(deliv_content, encoding="utf-8")
            self.state_db.update_task(task_id, dev_output=dev_output)
            self.state_db.log_agent_event(
                "STAGE_FINISH",
                "dev",
                f"Task #{task_id}: Kode selesai dibuat dan disimpan di docs/deliverables/{deliv_file.name}",
            )

            # Jalankan agen spesialis jika ada
            for spec_agent in specialists:
                self.state_db.log_agent_event(
                    "STAGE_START",
                    spec_agent,
                    f"Task #{task_id}: Review dan optimasi spesialis",
                )
                self.router.run_agent(
                    spec_agent,
                    f"Tinjau dan optimalkan implementasi pada branch {branch_name}: {dev_output}",
                )
                self.state_db.log_agent_event(
                    "STAGE_FINISH",
                    spec_agent,
                    f"Task #{task_id}: Review spesialis selesai",
                )

            # Jalankan QA & DevOps Verification
            self.state_db.update_task(task_id, current_stage=f"OPS_QA_ATTEMPT_{attempt}")
            self.state_db.log_agent_event(
                "STAGE_START",
                "ops",
                f"Task #{task_id}: Menjalankan unit test & verifikasi QA (Percobaan #{attempt})",
            )
            ops_res = self.router.run_agent(
                "ops",
                f"Jalankan unit test dan periksa telemetri kesehatan sistem untuk branch {branch_name}.",
            )
            last_ops_output = ops_res["output"]
            self.state_db.update_task(task_id, ops_output=last_ops_output)
            self.state_db.log_agent_event(
                "STAGE_FINISH",
                "ops",
                f"Task #{task_id}: QA Selesai - {last_ops_output[:80]}",
            )

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
            self.state_db.log_agent_event(
                "TASK_FAILED",
                "em",
                f"Task #{task_id} gagal QA setelah {self.max_retries}x percobaan",
            )
            notify(
                f"❌ **[EM-Agent]** Task #{task_id} gagal lulus QA setelah {self.max_retries}x percobaan. "
                f"Butuh arahan CTO."
            )
            return self.state_db.get_task(task_id) or {}

        # Tahap 5: Isolasi Git Branch & Commit Deliverables
        if self._is_git_repo():
            curr_branch = self._get_current_branch()
            # Buat atau checkout branch khusus task
            self._run_git(["checkout", "-B", branch_name])
            self._run_git(["add", "docs/prd", "docs/architecture", "docs/deliverables"])
            commit_msg = (
                f"feat(task-{task_id}): {short_title}\n\n"
                f"Deliverables generated by PakDir AI team:\n"
                f"- docs/prd/{prd_file.name}\n"
                f"- docs/architecture/specs/{arch_file.name}\n"
                f"- docs/deliverables/{deliv_file.name}"
            )
            code, status_out = self._run_git(["status", "--porcelain"])
            if status_out:
                self._run_git(["commit", "-m", commit_msg])
            # Kembalikan workspace ke branch awal (main) agar host tetap bersih
            self._run_git(["checkout", curr_branch])

        # Tahap 6: Masuk Gerbang Approval CTO
        summary_report = (
            f"Branch `{branch_name}` lulus QA (Percobaan ke-{attempt}).\n"
            f"- Spesialis terlibat: {', '.join(specialists) if specialists else 'Tim Inti'}\n"
            f"- Berkas Deliverable:\n"
            f"  • `docs/prd/{prd_file.name}`\n"
            f"  • `docs/architecture/specs/{arch_file.name}`\n"
            f"  • `docs/deliverables/{deliv_file.name}`\n"
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
        self.state_db.log_agent_event(
            "READY_FOR_APPROVAL",
            "em",
            f"Task #{task_id} siap di-approve CTO (Branch: {branch_name})",
        )
        notify(
            f"✅ **[EM-Agent]** Task #{task_id} siap! Ketik `!approve {task_id}` untuk Merge & Deploy, "
            f"`!taskinfo {task_id}` untuk cek bukti kerja, atau `!reject {task_id} <alasan>` untuk revisi."
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

        commit_hash = ""
        merged_files: List[str] = []

        if self._is_git_repo():
            # 1. Pastikan checkout main
            self._run_git(["checkout", "main"])
            # 2. Merge branch task dengan no-ff
            branch = task["branch_name"]
            code, merge_out = self._run_git([
                "merge", "--no-ff", branch,
                "-m", f"deploy: merge task #{task_id} ({task['title']}) approved by CTO\n\nCatatan CTO: {cto_note}"
            ])
            if code == 0:
                _, head_hash = self._run_git(["rev-parse", "--short", "HEAD"])
                commit_hash = head_hash.strip()
                _, files_out = self._run_git(["diff", "--name-only", "HEAD~1", "HEAD"])
                merged_files = [f.strip() for f in files_out.splitlines() if f.strip()]
            else:
                commit_hash = f"merge-pending-{task_id}"
                merged_files = [f"Conflict/Warning: {merge_out[:100]}"]
        else:
            commit_hash = f"sandbox-task-{task_id}"
            merged_files = [
                f"docs/prd/task_{task_id}_*.md",
                f"docs/architecture/specs/task_{task_id}_design.md",
                f"docs/deliverables/task_{task_id}_implementation.md",
            ]

        ops_deploy = self.router.run_agent(
            "ops",
            (
                f"CTO telah memberikan APPROVAL untuk Task #{task_id}. "
                f"Merge branch {task['branch_name']} ke main (Commit: {commit_hash}) dan jalankan smoke test."
            ),
        )

        deploy_summary = (
            f"{task['summary']}\n"
            f"[DEPLOYED - Commit: {commit_hash}]: {ops_deploy['output']}"
        )
        self.state_db.update_task(
            task_id,
            status="APPROVED_AND_DEPLOYED",
            current_stage="PRODUCTION",
            cto_feedback=cto_note,
            commit_hash=commit_hash,
            summary=deploy_summary,
        )
        self.state_db.log_agent_event(
            "TASK_DEPLOYED",
            "ops",
            f"Task #{task_id} di-merge ke main (Commit: {commit_hash}). Catatan CTO: {cto_note}",
        )
        res = self.state_db.get_task(task_id) or {}
        res["merged_files"] = merged_files
        return res

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
        self.state_db.log_agent_event(
            "TASK_REJECTED",
            "em",
            f"Task #{task_id} ditolak CTO. Alasan: {reason}",
        )
        return self.state_db.get_task(task_id) or {}
