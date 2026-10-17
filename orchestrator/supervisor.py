import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from orchestrator.agent_factory import AgentFactory
from orchestrator.code_extractor import extract_code_blocks, write_extracted_code_files
from orchestrator.llm_router import FreeLLMRouter
from orchestrator.qa_runner import run_real_qa
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
        qa_runner: Optional[Callable[[Path, List[Path]], Tuple[int, str]]] = None,
        projects_dir: Optional[str | Path] = None,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.state_db = state_db or StateDB(self.root_dir / "data" / "company_state.db")
        self.router = router or FreeLLMRouter(state_db=self.state_db)
        self.factory = factory or AgentFactory(root_dir=self.root_dir, state_db=self.state_db)
        self.max_retries = max_retries
        self.qa_runner = qa_runner or (lambda root, files: run_real_qa(root, written_files=files))

        if projects_dir:
            self.projects_dir = Path(projects_dir).resolve()
        elif os.getenv("PROJECTS_DIR"):
            self.projects_dir = Path(os.getenv("PROJECTS_DIR")).resolve()
        elif self.root_dir.name == "pak-dir" or (self.root_dir / "orchestrator").exists():
            # Isolasi otomatis: proyek pengguna berada di ../projects agar pak-dir tidak tercemar
            self.projects_dir = (self.root_dir.parent / "projects").resolve()
        else:
            self.projects_dir = self.root_dir

    def _run_git_cwd(self, args: List[str], cwd: Path) -> Tuple[int, str]:
        """Menjalankan perintah git pada direktori kerja cwd tertentu."""
        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"] = "PakDir AI"
        env["GIT_AUTHOR_EMAIL"] = "bot@pakdir.ai"
        env["GIT_COMMITTER_NAME"] = "PakDir AI"
        env["GIT_COMMITTER_EMAIL"] = "bot@pakdir.ai"
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "safe.directory"
        env["GIT_CONFIG_VALUE_0"] = "*"
        try:
            proc = subprocess.run(
                ["git", "-c", "safe.directory=*"] + args,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                env=env,
                timeout=20,
            )
            output = proc.stdout.strip() or proc.stderr.strip()
            return proc.returncode, output
        except Exception as exc:
            return 1, str(exc)

    def _run_git(self, args: List[str]) -> Tuple[int, str]:
        """Menjalankan perintah git pada root_dir orkestrator."""
        return self._run_git_cwd(args, self.root_dir)

    def _is_git_repo_at(self, directory: Path) -> bool:
        if not directory.exists():
            return False
        code, out = self._run_git_cwd(["rev-parse", "--is-inside-work-tree"], directory)
        return code == 0 and out.strip() == "true"

    def _is_git_repo(self) -> bool:
        return self._is_git_repo_at(self.root_dir)

    def _get_current_branch(self) -> str:
        code, out = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        return out.strip() if code == 0 and out.strip() else "main"

    def _resolve_task_project_dir(self, instruction: str, slug: str) -> Path:
        """
        Menentukan direktori proyek fisik di server.
        Memastikan proyek pengguna (website, api, tools) dibangun di luar direktori pak-dir.
        """
        target_match = re.search(
            r"(?:\[|\b)(?:target|folder|dir|path)\s*[:=]\s*([a-zA-Z]:[\\/][^\s\]]+|[/~][^\s\]]+|[a-zA-Z0-9_\-\.\/]+)",
            instruction,
            re.IGNORECASE,
        )
        if target_match:
            raw_p = target_match.group(1).strip()
            custom_path = Path(os.path.expanduser(raw_p)).resolve()
            return custom_path

        if self.projects_dir != self.root_dir:
            return (self.projects_dir / slug).resolve()

        return self.root_dir

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
        slug = branch_name.replace("feat/", "")
        target_dir = self._resolve_task_project_dir(instruction, slug)
        target_dir.mkdir(parents=True, exist_ok=True)

        task_id = self.state_db.create_task(
            title=short_title,
            instruction=instruction,
            branch_name=branch_name,
            project_dir=str(target_dir),
        )

        self.state_db.log_agent_event(
            "MANDATE_RECEIVED",
            "em",
            f"Menerima Task #{task_id}: {short_title} (Branch: {branch_name}, Lokasi: {target_dir})",
        )
        notify(
            f"👔 **[EM-Agent]** Menerima Task #{task_id}: *{short_title}* (Branch: `{branch_name}`)\n"
            f"📂 **[Workspace]** Direktori Proyek di Server: `{target_dir}`"
        )

        is_git_target = self._is_git_repo_at(target_dir) if target_dir != self.root_dir else self._is_git_repo()
        git_cwd = target_dir if target_dir != self.root_dir else self.root_dir
        curr_branch = "main"
        if is_git_target:
            code_b, out_b = self._run_git_cwd(["rev-parse", "--abbrev-ref", "HEAD"], git_cwd)
            curr_branch = out_b.strip() if code_b == 0 and out_b.strip() else "main"
            self._run_git_cwd(["checkout", "-B", branch_name], git_cwd)

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
        prd_dir = self.root_dir / "docs" / "prd"
        prd_dir.mkdir(parents=True, exist_ok=True)
        prd_file = prd_dir / f"task_{task_id}_{slug}.md"
        prd_content = (
            f"# PRD Task #{task_id}: {short_title}\n\n"
            f"- **Branch:** `{branch_name}`\n"
            f"- **Direktori Proyek di Server:** `{target_dir}`\n"
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
                f"Proyek ini dibangun di direktori server terpisah: `{target_dir}` (di luar pak-dir).\n"
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
            f"- **Direktori Proyek di Server:** `{target_dir}`\n"
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
        all_created_files: List[Path] = []

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
                f"Task #{task_id}: Menulis kode implementasi di {target_dir} (Percobaan #{attempt})",
            )

            fix_context = (
                f"\nPerbaiki error QA berikut dari percobaan sebelumnya:\n{last_ops_output}"
                if last_ops_output
                else ""
            )
            dev_prompt = (
                f"Implementasikan fitur di direktori server `{target_dir}` sesuai desain Architect:\n"
                f"{arch_res['output']}{fix_context}\n\n"
                f"LOKASI KERJA PROYEK DI SERVER:\n"
                f"- Direktori Proyek: `{target_dir}`\n"
                f"- PakDir adalah sistem orkestrator terpisah dan TIDAK BOLEH dimodifikasi.\n\n"
                "PANDUAN FORMAT BERKAS SUMBER KODE:\n"
                "Tuliskan seluruh berkas kode yang dibuat/dimodifikasi menggunakan format terstruktur berikut agar dapat ditulis langsung ke sistem:\n"
                "### File: path/ke/berkas.ext\n"
                "```bahasa\n"
                "// kode lengkap di sini\n"
                "```\n"
                "Sertakan berkas unit test di direktori tests/ jika diperlukan."
            )
            dev_res = self.router.run_agent("dev", dev_prompt)
            dev_output = dev_res["output"]

            # Ekstrak dan tulis berkas fisik nyata ke direktori proyek target
            blocks = extract_code_blocks(dev_output)
            written = write_extracted_code_files(blocks, target_dir)
            for w in written:
                if w not in all_created_files:
                    all_created_files.append(w)

            # Simpan deliverable implementasi kode
            deliv_content = (
                f"# Implementasi Deliverable Task #{task_id}: {short_title}\n\n"
                f"- **Branch:** `{branch_name}`\n"
                f"- **Direktori Proyek di Server:** `{target_dir}`\n"
                f"- **Attempt:** {attempt}\n"
                f"- **Model Generator:** `{dev_res['model']}`\n\n"
                f"## Berkas Kode yang Dihasilkan Fisik:\n"
            )
            if all_created_files:
                for f in all_created_files:
                    try:
                        rel = f.relative_to(target_dir)
                    except Exception:
                        rel = f
                    deliv_content += f"- `{rel}`\n"
            else:
                deliv_content += "- *(Tidak ada blok kode berkas yang terdeteksi)*\n"

            deliv_content += f"\n## Ringkasan Perubahan & Log Agen\n\n{dev_output}\n"
            deliv_file.write_text(deliv_content, encoding="utf-8")
            self.state_db.update_task(task_id, dev_output=dev_output)
            self.state_db.log_agent_event(
                "STAGE_FINISH",
                "dev",
                f"Task #{task_id}: Kode selesai dibuat ({len(all_created_files)} berkas nyata di {target_dir}) dan dicatat di docs/deliverables/{deliv_file.name}",
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
                    f"Tinjau dan optimalkan implementasi pada proyek {target_dir}: {dev_output}",
                )
                self.state_db.log_agent_event(
                    "STAGE_FINISH",
                    spec_agent,
                    f"Task #{task_id}: Review spesialis selesai",
                )

            # Jalankan QA Otomatis Nyata di Direktori Proyek Target
            self.state_db.update_task(task_id, current_stage=f"OPS_QA_ATTEMPT_{attempt}")
            self.state_db.log_agent_event(
                "STAGE_START",
                "ops",
                f"Task #{task_id}: Menjalankan unit test & verifikasi QA nyata di {target_dir} (Percobaan #{attempt})",
            )

            real_qa_code, real_qa_log = self.qa_runner(target_dir, all_created_files)

            # Minta @ops memvalidasi dan menganalisis hasil QA
            ops_prompt = (
                f"Berikut adalah hasil pengujian otomatis nyata di `{target_dir}` untuk branch {branch_name}:\n"
                f"- Exit Code: {real_qa_code}\n"
                f"- Log Pengujian:\n{real_qa_log}\n\n"
                f"Lakukan evaluasi QA, periksa stabilitas telemetri sistem, dan laporkan status kelayakan deployment."
            )
            ops_res = self.router.run_agent("ops", ops_prompt)
            last_ops_output = (
                f"[Real QA Test Exit Code: {real_qa_code}]\n"
                f"{real_qa_log}\n\n"
                f"[@ops Review]:\n{ops_res['output']}"
            )
            self.state_db.update_task(task_id, ops_output=last_ops_output)
            self.state_db.log_agent_event(
                "STAGE_FINISH",
                "ops",
                f"Task #{task_id}: QA Selesai - ExitCode={real_qa_code}",
            )

            # Lulus HANYA JIKA exit code == 0 DAN output ops tidak mengindikasikan kegagalan fatal
            if real_qa_code == 0 and "FAIL" not in ops_res["output"].upper() and "ERROR:" not in ops_res["output"].upper():
                qa_passed = True
                break

            notify(
                f"⚠️ **[EM-Agent Self-Healing]** Percobaan #{attempt} menemukan kendala QA (Exit Code: {real_qa_code}). "
                f"Menginstruksikan `@dev` memperbaiki otomatis..."
            )

        if not qa_passed:
            if is_git_target:
                self._run_git_cwd(["checkout", curr_branch], git_cwd)
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
                f"❌ **[EM-Agent]** Task #{task_id} gagal lulus QA setelah {self.max_retries}x percobaan.\n"
                f"Butuh arahan CTO: ketik `!retry {task_id} [instruksi perbaikan]` untuk melanjutkan."
            )
            return self.state_db.get_task(task_id) or {}

        # Tahap 5: Isolasi Git Branch & Commit Deliverables + Source Code Nyata
        if is_git_target:
            files_to_add = []
            if target_dir == self.root_dir:
                files_to_add.extend(["docs/prd", "docs/architecture", "docs/deliverables"])
            for f in all_created_files:
                try:
                    rel_f = str(f.relative_to(git_cwd)).replace("\\", "/")
                    files_to_add.append(rel_f)
                except Exception:
                    files_to_add.append(str(f))
            if files_to_add:
                self._run_git_cwd(["add"] + files_to_add, git_cwd)

            code_files_str = "\n".join(f"- {f.relative_to(git_cwd)}" for f in all_created_files) if all_created_files else "- Docs only"
            commit_msg = (
                f"feat(task-{task_id}): {short_title}\n\n"
                f"Deliverables & code generated by PakDir AI team:\n"
                f"- docs/prd/{prd_file.name}\n"
                f"- docs/architecture/specs/{arch_file.name}\n"
                f"- docs/deliverables/{deliv_file.name}\n"
                f"Code files:\n{code_files_str}"
            )
            code, status_out = self._run_git_cwd(["status", "--porcelain"], git_cwd)
            if status_out:
                self._run_git_cwd(["commit", "-m", commit_msg], git_cwd)
            self._run_git_cwd(["checkout", curr_branch], git_cwd)

        # Tahap 6: Masuk Gerbang Approval CTO
        code_count = len(all_created_files)
        summary_report = (
            f"Proyek di `{target_dir}` lulus QA nyata (Percobaan ke-{attempt}).\n"
            f"- Direktori Proyek di Server: `{target_dir}`\n"
            f"- Berkas Kode Dihasilkan: {code_count} berkas\n"
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
            f"Task #{task_id} siap di-approve CTO (Lokasi: {target_dir})",
        )
        notify(
            f"✅ **[EM-Agent]** Task #{task_id} siap! Ketik `!approve {task_id}` untuk Deploy ke server, "
            f"`!taskinfo {task_id}` untuk cek bukti kerja, atau `!reject {task_id} <alasan>` untuk revisi."
        )
        return self.state_db.get_task(task_id) or {}

    def resume_task(
        self,
        task_id: int,
        additional_instruction: str = "",
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """
        Melanjutkan kembali task yang berhenti (karena QA gagal, ditolak CTO, atau interrupted).
        Memicu loop perbaikan kode oleh @dev dan verifikasi QA oleh @ops hingga siap diajukan kembali ke CTO.
        """
        def notify(msg: str) -> None:
            if progress_callback:
                progress_callback(msg)

        task = self.state_db.get_task(task_id)
        if not task:
            raise KeyError(f"Task #{task_id} tidak ditemukan.")

        if task["status"] == "APPROVED_AND_DEPLOYED" and not additional_instruction:
            notify(
                f"ℹ️ Task #{task_id} sudah selesai dan di-deploy ke produksi. "
                f"Jika ingin melakukan revisi baru, sertakan instruksi: `!retry {task_id} <catatan>`"
            )
            return task

        short_title = task["title"]
        branch_name = task.get("branch_name", "feat/update")
        slug = branch_name.replace("feat/", "")
        target_dir = Path(task.get("project_dir")) if task.get("project_dir") else self._resolve_task_project_dir(task["instruction"], slug)
        target_dir.mkdir(parents=True, exist_ok=True)

        # Rangkum feedback dan instruksi perbaikan
        feedback_lines = []
        if task.get("cto_feedback"):
            feedback_lines.append(f"Catatan Revisi CTO Sebelumnya: {task['cto_feedback']}")
        if additional_instruction:
            feedback_lines.append(f"Arahan Baru dari CTO: {additional_instruction}")
        if task.get("ops_output"):
            feedback_lines.append(f"Hasil Pengujian QA Terakhir:\n{task['ops_output']}")
        combined_feedback = "\n\n".join(feedback_lines)

        self.state_db.update_task(
            task_id,
            status="IN_PROGRESS",
            current_stage="RESUMED_DEV",
            cto_feedback=combined_feedback,
        )
        self.state_db.log_agent_event(
            "TASK_RESUMED",
            "em",
            f"Task #{task_id} dilanjutkan kembali oleh CTO. Catatan: {additional_instruction[:100]}",
        )
        notify(f"👔 **[EM-Agent]** Melanjutkan pengerjaan Task #{task_id}: *{short_title}*...")
        notify(f"📂 **[Workspace]** Direktori Proyek di Server: `{target_dir}`")

        # Jalankan loop perbaikan Dev + Ops
        initial_retry = task.get("retry_count", 0)
        attempt = 0
        qa_passed = False
        last_ops_output = ""
        dev_output = ""
        all_created_files: List[Path] = []

        deliv_dir = self.root_dir / "docs" / "deliverables"
        deliv_dir.mkdir(parents=True, exist_ok=True)
        deliv_file = deliv_dir / f"task_{task_id}_implementation.md"

        while attempt < self.max_retries:
            attempt += 1
            current_total_retries = initial_retry + attempt
            self.state_db.update_task(
                task_id,
                current_stage=f"DEV_RETRY_ATTEMPT_{attempt}",
                retry_count=current_total_retries,
            )
            self.state_db.log_agent_event(
                "STAGE_START",
                "dev",
                f"Task #{task_id}: Menulis perbaikan kode di {target_dir} (Percobaan #{attempt})",
            )
            notify(f"🛠️ **[@dev]** Melakukan revisi dan perbaikan kode di `{target_dir}` (Percobaan #{attempt})...")

            fix_context = f"\nARAHAN REVISI & PERBAIKAN:\n{combined_feedback}\n" if combined_feedback else ""
            if last_ops_output:
                fix_context += f"\nERROR QA SEBELUMNYA:\n{last_ops_output}\n"

            dev_prompt = (
                f"Lanjutkan perbaikan kode untuk task '{short_title}' di direktori `{target_dir}`:\n"
                f"Instruksi Asli: {task['instruction']}\n"
                f"Rancangan Arsitektur: {task.get('arch_output', '')}\n"
                f"{fix_context}\n\n"
                "PANDUAN FORMAT BERKAS SUMBER KODE:\n"
                "Tuliskan seluruh berkas kode yang dibuat/dimodifikasi menggunakan format terstruktur berikut agar dapat ditulis langsung ke sistem:\n"
                "### File: path/ke/berkas.ext\n"
                "```bahasa\n"
                "// kode lengkap di sini\n"
                "```\n"
                "Sertakan berkas unit test di direktori tests/ jika diperlukan."
            )
            dev_res = self.router.run_agent("dev", dev_prompt)
            dev_output = dev_res["output"]

            blocks = extract_code_blocks(dev_output)
            written = write_extracted_code_files(blocks, target_dir)
            for w in written:
                if w not in all_created_files:
                    all_created_files.append(w)

            # Update deliverable file
            deliv_content = (
                f"# Revisi Deliverable Task #{task_id}: {short_title}\n\n"
                f"- **Direktori Proyek:** `{target_dir}`\n"
                f"- **Attempt Revisi:** {attempt}\n"
                f"- **Model Generator:** `{dev_res['model']}`\n\n"
                f"## Berkas Kode:\n"
            )
            for f in all_created_files:
                try:
                    rel = f.relative_to(target_dir)
                except Exception:
                    rel = f
                deliv_content += f"- `{rel}`\n"
            deliv_content += f"\n## Ringkasan Perubahan:\n\n{dev_output}\n"
            deliv_file.write_text(deliv_content, encoding="utf-8")
            self.state_db.update_task(task_id, dev_output=dev_output)

            # Jalankan QA
            self.state_db.update_task(task_id, current_stage=f"OPS_QA_RETRY_{attempt}")
            real_qa_code, real_qa_log = self.qa_runner(target_dir, all_created_files)
            ops_prompt = (
                f"Berikut hasil pengujian otomatis nyata di `{target_dir}`:\n"
                f"- Exit Code: {real_qa_code}\n"
                f"- Log Pengujian:\n{real_qa_log}\n\n"
                f"Lakukan evaluasi QA dan laporkan status kelayakan deployment."
            )
            ops_res = self.router.run_agent("ops", ops_prompt)
            last_ops_output = f"[Real QA Test Exit Code: {real_qa_code}]\n{real_qa_log}\n\n[@ops Review]:\n{ops_res['output']}"
            self.state_db.update_task(task_id, ops_output=last_ops_output)

            if real_qa_code == 0 and "FAIL" not in ops_res["output"].upper() and "ERROR:" not in ops_res["output"].upper():
                qa_passed = True
                break

            notify(
                f"⚠️ **[EM-Agent Self-Healing]** Revisi #{attempt} QA menemukan kendala (Exit Code: {real_qa_code}). "
                f"Menginstruksikan `@dev` memperbaiki..."
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
                f"Task #{task_id} gagal QA setelah revisi {self.max_retries}x percobaan",
            )
            notify(
                f"❌ **[EM-Agent]** Task #{task_id} masih belum lulus QA setelah {self.max_retries}x percobaan revisi.\n"
                f"Butuh arahan CTO: ketik `!retry {task_id} <instruksi tambahan>`"
            )
            return self.state_db.get_task(task_id) or {}

        # Sukses QA
        code_count = len(all_created_files)
        summary_report = (
            f"Revisi Task #{task_id} di `{target_dir}` berhasil lulus QA nyata (Percobaan ke-{attempt}).\n"
            f"- Direktori Proyek di Server: `{target_dir}`\n"
            f"- Berkas Kode Dihasilkan: {code_count} berkas\n"
            f"- Ringkasan QA/Ops: {last_ops_output[:250]}"
        )
        self.state_db.update_task(
            task_id,
            status="WAITING_CTO_APPROVAL",
            current_stage="CTO_GATE",
            summary=summary_report,
        )
        self.state_db.log_agent_event(
            "READY_FOR_APPROVAL",
            "em",
            f"Task #{task_id} siap di-approve CTO setelah revisi",
        )
        notify(
            f"✅ **[EM-Agent]** Task #{task_id} berhasil diperbaiki dan lulus QA!\n"
            f"• **Lokasi Proyek:** `{target_dir}`\n"
            f"• Ketik `!approve {task_id}` untuk Deploy, atau `!taskinfo {task_id}` untuk cek bukti kerja."
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

        slug = task.get("branch_name", "feat/task").replace("feat/", "")
        target_dir = Path(task.get("project_dir")) if task.get("project_dir") else self._resolve_task_project_dir(task["instruction"], slug)
        commit_hash = ""
        merged_files: List[str] = []

        is_git_target = self._is_git_repo_at(target_dir) if target_dir != self.root_dir else self._is_git_repo()
        git_cwd = target_dir if target_dir != self.root_dir else self.root_dir

        if is_git_target:
            self._run_git_cwd(["checkout", "main"], git_cwd)
            branch = task["branch_name"]
            code, merge_out = self._run_git_cwd([
                "merge", "--no-ff", branch,
                "-m", f"deploy: merge task #{task_id} ({task['title']}) approved by CTO\n\nCatatan CTO: {cto_note}"
            ], git_cwd)
            if code == 0:
                _, head_hash = self._run_git_cwd(["rev-parse", "--short", "HEAD"], git_cwd)
                commit_hash = head_hash.strip()
                _, files_out = self._run_git_cwd(["diff", "--name-only", "HEAD~1", "HEAD"], git_cwd)
                merged_files = [f.strip() for f in files_out.splitlines() if f.strip()]
            else:
                self._run_git_cwd(["merge", "--abort"], git_cwd)
                raise RuntimeError(
                    f"Gagal melakukan merge branch '{branch}' ke 'main' karena terjadi konflik: {merge_out}"
                )
        else:
            commit_hash = f"deployed-task-{task_id}"
            merged_files = [
                f"docs/prd/task_{task_id}_*.md",
                f"docs/architecture/specs/task_{task_id}_design.md",
                f"docs/deliverables/task_{task_id}_implementation.md",
            ]

        ops_deploy = self.router.run_agent(
            "ops",
            (
                f"CTO telah memberikan APPROVAL untuk Task #{task_id}. "
                f"Proyek berada di `{target_dir}`. "
                f"Lakukan konfirmasi deployment (Commit: {commit_hash}) dan berikan panduan cara menjalankan aplikasi di server."
            ),
        )

        deploy_summary = (
            f"{task['summary']}\n"
            f"[DEPLOYED - Lokasi: {target_dir} - Commit: {commit_hash}]: {ops_deploy['output']}"
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
            f"Task #{task_id} di-deploy ke `{target_dir}` (Commit: {commit_hash}). Catatan CTO: {cto_note}",
        )
        res = self.state_db.get_task(task_id) or {}
        res["merged_files"] = merged_files
        res["project_dir"] = str(target_dir)
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
