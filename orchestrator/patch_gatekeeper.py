import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from orchestrator.code_extractor import extract_code_blocks, write_extracted_code_files
from orchestrator.llm_router import FreeLLMRouter
from orchestrator.qa_runner import run_real_qa
from orchestrator.state_db import StateDB
from orchestrator.system_context import get_live_server_context, restart_system_service


class PatchGatekeeper:
    """
    Mesin Pengaman Rilis & Hotfix (Delivery Gatekeeper).
    Mengatur alur perbaikan bug, patch konfigurasi, atau rilis fitur terarah:
    1. Mengisolasi perubahan di branch `hotfix/*` atau `feat/*`.
    2. Menghasilkan patch terarah (bukan merusak seluruh struktur repo).
    3. Menjalankan verifikasi sintaksis dan pengujian QA otomatis.
    4. Mengunci rilis di Gerbang Persetujuan CTO (`WAITING_CTO_APPROVAL`).
    5. Melakukan merge `--no-ff` ke `main` dan memicu reload service hanya setelah persetujuan eksplisit.
    """

    def __init__(
        self,
        root_dir: str | Path = ".",
        state_db: Optional[StateDB] = None,
        router: Optional[FreeLLMRouter] = None,
        qa_runner: Optional[Callable[[Path, List[Path]], Tuple[int, str]]] = None,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.state_db = state_db or StateDB(self.root_dir / "data" / "company_state.db")
        self.router = router or FreeLLMRouter(state_db=self.state_db)
        self.qa_runner = qa_runner or (lambda root, files: run_real_qa(root, written_files=files))

    def _run_git(self, args: List[str], cwd: Optional[Path] = None) -> Tuple[int, str]:
        work_dir = cwd or self.root_dir
        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"] = "PakDir Sentinel"
        env["GIT_AUTHOR_EMAIL"] = "sentinel@pakdir.ai"
        env["GIT_COMMITTER_NAME"] = "PakDir Sentinel"
        env["GIT_COMMITTER_EMAIL"] = "sentinel@pakdir.ai"
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "safe.directory"
        env["GIT_CONFIG_VALUE_0"] = "*"
        try:
            proc = subprocess.run(
                ["git", "-c", "safe.directory=*"] + args,
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                env=env,
                timeout=20,
            )
            output = proc.stdout.strip() or proc.stderr.strip()
            return proc.returncode, output
        except Exception as exc:
            return 1, str(exc)

    def _is_git_repo(self, directory: Optional[Path] = None) -> bool:
        work_dir = directory or self.root_dir
        if not work_dir.exists():
            return False
        code, out = self._run_git(["rev-parse", "--is-inside-work-tree"], cwd=work_dir)
        return code == 0 and out.strip() == "true"

    @staticmethod
    def _slugify(title: str, prefix: str = "hotfix") -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return f"{prefix}/{slug[:30] or 'patch'}"

    def prepare_hotfix(
        self,
        instruction: str,
        target_dir: Optional[Path] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """
        Mempersiapkan hotfix atau patch konfigurasi:
        1. Membuat branch git hotfix/<slug>.
        2. Meminta AI merumuskan berkas kode/konfigurasi yang diubah.
        3. Menulis file dan menjalankan QA syntax check.
        4. Meng-commit ke branch dan menaruh task ke antrean WAITING_CTO_APPROVAL.
        """
        def notify(msg: str) -> None:
            if progress_callback:
                progress_callback(msg)

        work_dir = Path(target_dir).resolve() if target_dir else self.root_dir
        short_title = instruction.strip().split("\n")[0][:60]
        branch_name = self._slugify(short_title, prefix="hotfix")

        task_id = self.state_db.create_task(
            title=short_title,
            instruction=instruction,
            branch_name=branch_name,
            project_dir=str(work_dir),
        )

        notify(f"🛡️ **[Gatekeeper]** Membuka Task #{task_id}: `{short_title}` di branch `{branch_name}`...")

        is_git = self._is_git_repo(work_dir)
        curr_branch = "main"
        if is_git:
            code_b, out_b = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=work_dir)
            curr_branch = out_b.strip() if code_b == 0 and out_b.strip() else "main"
            self._run_git(["checkout", "-B", branch_name], cwd=work_dir)

        # Minta @dev merumuskan file patch
        live_ctx = get_live_server_context()
        prompt = (
            f"TUGAS HOTFIX/PATCH:\n{instruction}\n\n"
            f"KONDISI HOST SAAT INI:\n{live_ctx}\n\n"
            "PANDUAN FORMAT BERKAS SUMBER KODE:\n"
            "Tuliskan berkas yang dibuat atau dimodifikasi menggunakan format terstruktur berikut:\n"
            "### File: relative/path/to/file.ext\n"
            "```bahasa\n"
            "// kode lengkap di sini\n"
            "```\n"
            "Pastikan kode bersih, valid secara sintaksis, dan hanya mengubah berkas yang relevan."
        )

        res = self.router.run_agent("dev", prompt)
        output_text = res["output"]

        # Ekstrak file dan tulis
        blocks = extract_code_blocks(output_text)
        written_files = write_extracted_code_files(blocks, work_dir)

        # Jalankan QA Sintaks
        qa_code, qa_log = self.qa_runner(work_dir, written_files)

        if qa_code != 0:
            notify(f"⚠️ **[QA Warning]** Pengujian sintaks menemukan isu: {qa_log[:150]}")

        # Simpan deliverable log
        deliv_dir = self.root_dir / "docs" / "deliverables"
        deliv_dir.mkdir(parents=True, exist_ok=True)
        deliv_file = deliv_dir / f"task_{task_id}_hotfix.md"
        deliv_file.write_text(
            f"# Hotfix Deliverable Task #{task_id}: {short_title}\n\n"
            f"- **Branch:** `{branch_name}`\n"
            f"- **Model:** `{res['model']}`\n"
            f"- **QA Exit Code:** {qa_code}\n\n"
            f"## Berkas Diubah:\n"
            + ("\n".join(f"- `{f.relative_to(work_dir)}`" for f in written_files) if written_files else "- Tidak ada berkas")
            + f"\n\n## Rincian Perubahan:\n{output_text}\n\n## Log QA:\n{qa_log}\n",
            encoding="utf-8",
        )

        # Commit ke branch
        if is_git:
            files_to_add = [str(f.relative_to(work_dir)).replace("\\", "/") for f in written_files]
            if work_dir == self.root_dir:
                files_to_add.append(f"docs/deliverables/{deliv_file.name}")
            if files_to_add:
                self._run_git(["add"] + files_to_add, cwd=work_dir)
                self._run_git(["commit", "-m", f"hotfix(task-{task_id}): {short_title}"], cwd=work_dir)
            self._run_git(["checkout", curr_branch], cwd=work_dir)

        summary_msg = (
            f"Hotfix siap di branch `{branch_name}` ({len(written_files)} berkas diubah).\n"
            f"- Status QA: {'✅ LULUS' if qa_code == 0 else f'⚠️ ExitCode={qa_code}'}\n"
            f"- Menunggu otorisasi CTO: ketik `!approve {task_id}` untuk deploy/merge."
        )

        self.state_db.update_task(
            task_id,
            status="WAITING_CTO_APPROVAL",
            current_stage="READY_FOR_APPROVAL",
            dev_output=output_text,
            ops_output=qa_log,
            summary=summary_msg,
        )

        notify(f"📋 **[Gatekeeper]** Task #{task_id} selesai dipersiapkan!\n{summary_msg}")
        return self.state_db.get_task(task_id) or {}

    def approve_and_deploy(self, task_id: int, note: str = "") -> Dict[str, Any]:
        """
        Menyetujui rilis task: melakukan merge git --no-ff ke main dan reload service jika relevan.
        """
        task = self.state_db.get_task(task_id)
        if not task:
            raise ValueError(f"Task #{task_id} tidak ditemukan.")

        if task["status"] != "WAITING_CTO_APPROVAL":
            raise ValueError(f"Task #{task_id} berstatus '{task['status']}', bukan WAITING_CTO_APPROVAL.")

        branch_name = task["branch_name"]
        work_dir = Path(task["project_dir"]).resolve() if task.get("project_dir") else self.root_dir

        commit_hash = ""
        if self._is_git_repo(work_dir):
            code_co, _ = self._run_git(["checkout", "main"], cwd=work_dir)
            if code_co != 0:
                self._run_git(["checkout", "master"], cwd=work_dir)

            code_mg, out_mg = self._run_git(["merge", "--no-ff", branch_name, "-m", f"deploy(task-{task_id}): {task['title']}"], cwd=work_dir)
            if code_mg != 0:
                raise RuntimeError(f"Gagal melakukan git merge: {out_mg}")

            code_rev, out_rev = self._run_git(["rev-parse", "--short", "HEAD"], cwd=work_dir)
            commit_hash = out_rev.strip() if code_rev == 0 else "merged"

        self.state_db.update_task(
            task_id,
            status="APPROVED_AND_DEPLOYED",
            current_stage="PRODUCTION",
            cto_feedback=note or "Approved by CTO",
            commit_hash=commit_hash,
        )
        self.state_db.log_agent_event(
            "TASK_APPROVED",
            "gatekeeper",
            f"Task #{task_id} di-approve oleh CTO (Commit: {commit_hash})",
        )
        return self.state_db.get_task(task_id) or {}

    def reject_task(self, task_id: int, reason: str = "") -> Dict[str, Any]:
        """Menolak task dan mengembalikan status ke REJECTED_BY_CTO."""
        task = self.state_db.get_task(task_id)
        if not task:
            raise ValueError(f"Task #{task_id} tidak ditemukan.")

        self.state_db.update_task(
            task_id,
            status="REJECTED_BY_CTO",
            current_stage="REJECTED",
            cto_feedback=reason or "Rejected by CTO",
        )
        self.state_db.log_agent_event(
            "TASK_REJECTED",
            "gatekeeper",
            f"Task #{task_id} ditolak CTO: {reason}",
        )
        return self.state_db.get_task(task_id) or {}
