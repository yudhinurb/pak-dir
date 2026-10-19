import asyncio
import os
import platform
import shutil
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

from orchestrator.agent_factory import AgentFactory
from orchestrator.llm_router import FreeLLMRouter, FREE_MODEL_POOLS
from orchestrator.log_investigator import LogInvestigator
from orchestrator.patch_gatekeeper import PatchGatekeeper
from orchestrator.state_db import StateDB
from orchestrator.supervisor import EMSupervisor
from orchestrator.watchdog import SentinelWatchdog
from orchestrator.system_context import (
    get_listening_ports,
    get_firewall_status,
    get_active_services,
    get_git_status,
    execute_server_command,
    get_security_audit_report,
    get_top_processes_by_ram,
    get_service_journal_logs,
    restart_system_service,
    clean_system_caches,
)
from orchestrator.file_processor import (
    process_single_attachment,
    format_attachments_context,
)


async def send_split_message(ctx: Any, text: str, max_chunk_size: int = 1800) -> None:
    """
    Mengirim pesan ke Discord dengan memecah pesan panjang menjadi beberapa bagian
    agar tidak terpotong oleh batas limit 2000 karakter Discord dan memastikan
    blok kode Markdown (```) tidak rusak di tengah.
    """
    text = str(text or "").strip()
    if not text:
        return

    if len(text) <= max_chunk_size:
        safe_text = text[:1950] if len(text) > 1950 else text
        await ctx.send(safe_text)
        return

    chunks: List[str] = []
    remaining = text

    while len(remaining) > max_chunk_size:
        # Cari titik pemisah terbaik di batas baris baru
        split_idx = remaining.rfind("\n", 0, max_chunk_size)
        if split_idx == -1 or split_idx < max_chunk_size // 3:
            # Jika tidak ada baris baru yang pas, cari spasi
            split_idx = remaining.rfind(" ", 0, max_chunk_size)
        if split_idx == -1 or split_idx <= 0:
            split_idx = max_chunk_size

        chunk = remaining[:split_idx].rstrip()
        remaining = remaining[split_idx:].lstrip()

        # Periksa apakah chunk ini memotong blok kode Markdown yang belum ditutup
        fence_count = chunk.count("```")
        if fence_count % 2 != 0:
            # Blok kode terbuka tapi belum tertutup di chunk ini -> tutup di sini, buka kembali di chunk berikutnya
            chunk += "\n```"
            remaining = "```\n" + remaining

        if chunk:
            chunks.append(chunk)

    if remaining:
        chunks.append(remaining)

    total_chunks = len(chunks)
    for idx, c in enumerate(chunks):
        if total_chunks > 1 and idx > 0:
            header = f"*(Lanjutan {idx + 1}/{total_chunks})*\n"
            msg_to_send = header + c
        else:
            msg_to_send = c

        # Jaminan mutlak: pesan tidak pernah melebihi 1950 karakter ke Discord API
        if len(msg_to_send) > 1950:
            msg_to_send = msg_to_send[:1940] + "\n```\n...(dipotong)"
        await ctx.send(msg_to_send)


def get_server_health_report() -> str:
    """
    Mengambil metrik kesehatan server (RAM, Swap, Disk, Port, Layanan, Git) secara ringan.
    """
    total_disk, used_disk, free_disk = shutil.disk_usage(".")
    disk_info = (
        f"{used_disk // (1024**3):.1f} GB / {total_disk // (1024**3):.1f} GB "
        f"(Bebas: {free_disk // (1024**3):.1f} GB)"
    )

    ram_info = "N/A"
    swap_info = "N/A"

    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        sm = psutil.swap_memory()
        ram_info = (
            f"{vm.used // (1024**2)} MB / {vm.total // (1024**2)} MB ({vm.percent}%)"
        )
        swap_info = (
            f"{sm.used // (1024**2)} MB / {sm.total // (1024**2)} MB ({sm.percent}%)"
        )
    except ImportError:
        if Path("/proc/meminfo").exists():
            mem: Dict[str, int] = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = int(parts[1].strip().split()[0]) // 1024
                    mem[key] = val
            total_m = mem.get("MemTotal", 0)
            avail_m = mem.get("MemAvailable", 0)
            used_m = max(0, total_m - avail_m)
            swap_total = mem.get("SwapTotal", 0)
            swap_free = mem.get("SwapFree", 0)
            ram_info = f"{used_m} MB / {total_m} MB (Tersedia: {avail_m} MB)"
            swap_info = f"{swap_total - swap_free} MB / {swap_total} MB"

    git_info = get_git_status()
    services_info = get_active_services()

    return (
        "🖥️ **Status Telemetri & Kesehatan Node CTO (PakDir)**\n"
        f"- **OS / Platform:** `{platform.system()} {platform.release()}`\n"
        f"- **Git Repository:** {git_info}\n"
        f"- **Layanan Kunci:** `{services_info}`\n"
        f"- **Pemakaian RAM:** `{ram_info}`\n"
        f"- **Pemakaian Swap:** `{swap_info}`\n"
        f"- **Pemakaian Disk:** `{disk_info}`\n"
        f"- **Pool Reasoning Models:** `{', '.join(FREE_MODEL_POOLS['reasoning'][:3])}...`\n"
        f"- **Pool Coding Models:** `{', '.join(FREE_MODEL_POOLS['coding'][:3])}...`\n"
        "*Ketik `!ports` untuk rincian port networking atau `!agents` untuk status tim AI.*"
    )


class CTOCommandHandler:
    """
    Handler logika perintah CTO yang dapat dipanggil dari Discord Bot maupun CLI/Test.
    """

    def __init__(self, root_dir: str | Path = ".", state_db: Optional[StateDB] = None) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.state_db = state_db or StateDB(self.root_dir / "data" / "company_state.db")
        self.router = FreeLLMRouter(state_db=self.state_db)
        self.factory = AgentFactory(root_dir=self.root_dir, state_db=self.state_db)
        self.supervisor = EMSupervisor(
            root_dir=self.root_dir,
            state_db=self.state_db,
            router=self.router,
            factory=self.factory,
        )
        self.watchdog = SentinelWatchdog(state_db=self.state_db)
        self.investigator = LogInvestigator(router=self.router, state_db=self.state_db)
        self.gatekeeper = PatchGatekeeper(root_dir=self.root_dir, state_db=self.state_db, router=self.router)

    def handle_top(self, limit: int = 5) -> str:
        procs = get_top_processes_by_ram(limit=limit)
        return f"📊 **Top {limit} Proses Pemakan RAM di Host:**\n```\n{procs}\n```"

    def handle_logs(self, service_name: str, lines: int = 40) -> str:
        logs = get_service_journal_logs(service_name, lines=lines)
        return f"📜 **Log Terakhir Layanan `{service_name}` ({lines} baris):**\n```\n{logs[-3500:]}\n```"

    def handle_investigate_service(self, service_name: str, lines: int = 50) -> str:
        res = self.investigator.investigate_service(service_name, lines=lines)
        return (
            f"🔍 **Laporan Investigasi Sistem — Layanan `{res['service']}`** *(Model: `{res['model']}`)*\n\n"
            f"{res['analysis']}"
        )

    def handle_investigate_log(self, log_content: str, label: str = "Uploaded Log") -> str:
        res = self.investigator.investigate_log_text(log_content, source_label=label)
        return (
            f"🔍 **Laporan Investigasi Log — `{res['source']}`** *(Model: `{res['model']}`)*\n\n"
            f"{res['analysis']}"
        )

    def handle_restart(self, service_name: str) -> str:
        code, out = restart_system_service(service_name)
        return out

    def handle_clean(self) -> str:
        return clean_system_caches(self.state_db.db_path)

    def handle_hotfix(self, instruction: str, progress_callback: Optional[Any] = None) -> Dict[str, Any]:
        return self.gatekeeper.prepare_hotfix(instruction, progress_callback=progress_callback)

    def handle_agents_list(self) -> str:
        agents = self.factory.list_agents()
        lines = ["👥 **Daftar Karyawan AI Aktif (`opencode.json`):**"]
        for ag in agents:
            tag = "Core" if ag["is_core"] else "Dynamic Specialist"
            tools = ", ".join(ag["tools"]) or "read-only"
            lines.append(f"- **`@{ag['name']}`** [{tag}] — {ag['description']} *(Tools: `{tools}`)*")
        return "\n".join(lines)

    def handle_hire(self, name: str, title: str, description: str, sop: str, tools: str = "bash") -> str:
        tool_list = [t.strip() for t in tools.split(",") if t.strip()]
        self.factory.hire_agent(
            name=name,
            title=title,
            description=description,
            sop_rules=sop,
            allowed_tools=tool_list,
        )
        return (
            f"🆕 **Karyawan AI Baru Direkrut!**\n"
            f"- **ID:** `@{name}` ({title})\n"
            f"- **Tugas:** {description}\n"
            f"- **Portal Org-Chart:** `docs/architecture/org-chart.md` otomatis diperbarui."
        )

    def handle_specialize(self, name: str, focus: str) -> str:
        res = self.factory.specialize_agent(name=name, additional_focus=focus)
        return (
            f"🎯 **SOP `@{res['name']}` Berhasil Dipertajam!**\n"
            f"- **Fokus Baru:** {res['updated_focus']}\n"
            f"- **File SOP:** `{res['prompt_file']}`"
        )

    def handle_ask(self, agent_name: str, question: str) -> str:
        res = self.router.run_agent(agent_name, question)
        return (
            f"💬 **Jawaban `@{res['agent']}`** *(via `{res['model']}`)*:\n"
            f"{res['output']}"
        )

    def handle_pending(self) -> str:
        pending = self.state_db.list_pending_approvals()
        if not pending:
            return "✅ Tidak ada antrean PR/Task yang menunggu persetujuan CTO saat ini."
        lines = ["⏳ **Daftar Task Menunggu Approval CTO:**"]
        for t in pending:
            lines.append(
                f"- **Task #{t['id']}** (`{t['branch_name']}`): *{t['title']}*\n"
                f"  Ketik `!approve {t['id']}` atau `!reject {t['id']} <alasan>`"
            )
        return "\n".join(lines)

    def handle_status(self) -> str:
        active_tasks = self.state_db.get_active_tasks()
        recent_tasks = self.state_db.get_recent_tasks(limit=5)
        recent_events = self.state_db.get_recent_agent_events(limit=1)
        open_incidents = self.state_db.get_open_incidents()
        mem_metrics = self.watchdog.get_memory_metrics()

        lines = ["📊 **STATUS REAL-TIME HOST & TIM SENTINEL (PakDir v2)**\n"]

        if mem_metrics.get("ram_total_mb", 0) > 0:
            lines.append(
                f"🖥️ **Host RAM:** {mem_metrics['ram_used_mb']} MB / {mem_metrics['ram_total_mb']} MB ({mem_metrics['ram_percent']}%) | "
                f"**Swap:** {mem_metrics['swap_percent']}%\n"
            )

        if open_incidents:
            lines.append(f"🚨 **Peringatan Sentinel Aktif ({len(open_incidents)} Insiden):**")
            for inc in open_incidents[:3]:
                first_line = inc['details'].splitlines()[0] if inc.get('details') else inc['alert_type']
                lines.append(f"• `[{inc['severity']}]` **{inc['alert_type']}**: {first_line[:90]}")
            lines.append("")

        if active_tasks:
            lines.append("⚡ **Tugas Sedang Berjalan / Menunggu Tindakan:**")
            for t in active_tasks:
                st = t["status"]
                if st == "WAITING_CTO_APPROVAL":
                    status_emoji = "⏳"
                    action_hint = f"👉 Siap di-deploy: ketik `!approve {t['id']}` atau cek bukti `!taskinfo {t['id']}`"
                elif st in ("NEEDS_CTO_INTERVENTION", "REJECTED_BY_CTO"):
                    status_emoji = "⚠️"
                    action_hint = f"👉 Berhenti / butuh arahan: ketik `!retry {t['id']} [catatan revisi]` untuk memicu kembali tim AI"
                else:
                    status_emoji = "🔄"
                    action_hint = f"👉 Sedang dikerjakan tim AI (Stage: `{t['current_stage']}`)"

                loc = f" | Lokasi: `{t['project_dir']}`" if t.get("project_dir") else ""
                lines.append(
                    f"{status_emoji} **Task #{t['id']}**: *{t['title']}*\n"
                    f"   • Status: `{t['status']}` | Stage: `{t['current_stage']}`{loc}\n"
                    f"   • {action_hint}"
                )
            lines.append("")
        else:
            lines.append("🟢 **Tugas Aktif:** Semua agen saat ini idle (tidak ada sprint/antrean berjalan).\n")

        if recent_tasks:
            lines.append("📁 **Riwayat 3 Tugas Terakhir:**")
            for t in recent_tasks[:3]:
                commit_info = f" (Commit: `{t.get('commit_hash', '')[:7]}`)" if t.get("commit_hash") else ""
                lines.append(
                    f"• **Task #{t['id']}** [{t['status']}]{commit_info}: {t['title']} — `{t['branch_name']}`"
                )
            lines.append("")

        if recent_events:
            ev = recent_events[0]
            dt = time.strftime("%H:%M:%S", time.localtime(ev["created_at"]))
            lines.append(f"📡 **Aktivitas Terakhir:** `[{dt}]` **@{ev['agent_name']}** (`{ev['action']}`): {ev['details'][:100]}")

        lines.append("\n*Ketik `!taskinfo <id>` untuk melihat bukti hasil kerja lengkap atau `!activity` untuk riwayat timeline.*")
        return "\n".join(lines)

    def handle_taskinfo(self, task_id: int) -> str:
        task = self.state_db.get_task(task_id)
        if not task:
            return f"❌ Task #{task_id} tidak ditemukan di database."

        created_dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(task["created_at"]))
        updated_dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(task["updated_at"]))
        commit_str = f"`{task.get('commit_hash')}`" if task.get("commit_hash") else "*(Belum di-merge / Menunggu Approval)*"
        proj_dir_str = f"`{task.get('project_dir')}`" if task.get("project_dir") else "*(Root)*"

        lines = [
            f"📋 **BUKTI HASIL KERJA RESMI — TASK #{task['id']}**",
            f"• **Judul:** {task['title']}",
            f"• **Status:** `{task['status']}` | **Tahapan:** `{task['current_stage']}`",
            f"• **Lokasi Proyek di Server:** {proj_dir_str}",
            f"• **Branch:** `{task['branch_name']}`",
            f"• **Merge Commit:** {commit_str}",
            f"• **Percobaan QA (Retries):** {task['retry_count']}x",
            f"• **Waktu Dibuat:** `{created_dt}` | **Terakhir Update:** `{updated_dt}`",
            "",
            "---",
            "📄 **1. Deliverable Product Manager (`@pm` - PRD)**:",
        ]
        pm_out = task.get("pm_output") or "*(Belum ada data PRD)*"
        lines.append(pm_out[:1200] + ("\n...(dipotong)" if len(pm_out) > 1200 else ""))
        lines.append("")

        lines.append("🏗️ **2. Deliverable Architect (`@architect` - Desain Sistem)**:")
        arch_out = task.get("arch_output") or "*(Belum ada data Desain)*"
        lines.append(arch_out[:1200] + ("\n...(dipotong)" if len(arch_out) > 1200 else ""))
        lines.append("")

        lines.append("💻 **3. Deliverable Developer (`@dev` - Implementasi & Kode)**:")
        dev_out = task.get("dev_output") or "*(Belum ada data Kode)*"
        lines.append(dev_out[:1200] + ("\n...(dipotong)" if len(dev_out) > 1200 else ""))
        lines.append("")

        lines.append("🧪 **4. Hasil QA & DevOps Verification (`@ops`)**:")
        ops_out = task.get("ops_output") or task.get("summary") or "*(Belum ada data QA)*"
        lines.append(ops_out[:1000] + ("\n...(dipotong)" if len(ops_out) > 1000 else ""))
        lines.append("")

        if task.get("cto_feedback"):
            lines.append(f"💬 **Catatan CTO:** {task['cto_feedback']}")

        lines.append("\n*Arsip berkas tersimpan fisik di direktori server `docs/prd/`, `docs/architecture/specs/`, dan `docs/deliverables/`.*")
        return "\n".join(lines)

    def handle_activity(self, limit: int = 10) -> str:
        events = self.state_db.get_recent_agent_events(limit=limit)
        if not events:
            return "ℹ️ Belum ada catatan aktivitas agen di sistem."

        lines = [f"📜 **LOG AKTIVITAS AGEN (Terbaru {len(events)} Event)**:\n"]
        for ev in events:
            dt = time.strftime("%H:%M:%S", time.localtime(ev["created_at"]))
            lines.append(f"• `[{dt}]` **@{ev['agent_name']}** (`{ev['action']}`): {ev['details']}")

        return "\n".join(lines)


def run_discord_bot() -> None:
    """
    Menjalankan Discord Bot untuk Ruang Kantor Virtual CTO.
    """
    try:
        import discord
        from discord.ext import commands, tasks
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError(
            "Paket discord.py / python-dotenv belum terinstal. Jalankan: pip install -r orchestrator/requirements.txt"
        ) from exc

    load_dotenv()
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN belum diisi di file .env!")

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
    handler = CTOCommandHandler(".")

    @tasks.loop(minutes=5)
    async def watchdog_heartbeat() -> None:
        """Pemeriksaan berkala otomatis oleh Anjing Penjaga (Watchdog Sentinel)."""
        try:
            alerts = await asyncio.to_thread(handler.watchdog.inspect_system)
            if alerts:
                # Cari text channel yang dapat dikirimi pesan
                target_channel = None
                for guild in bot.guilds:
                    for ch in guild.text_channels:
                        if ch.permissions_for(guild.me).send_messages:
                            target_channel = ch
                            break
                    if target_channel:
                        break

                if target_channel:
                    for alt in alerts:
                        msg = (
                            f"🚨 **[WATCHDOG SENTINEL ALERT]** {alt['title']}\n"
                            f"• **Tingkat Bahaya:** `{alt['severity']}`\n\n"
                            f"{alt['details']}"
                        )
                        await send_split_message(target_channel, msg)
        except Exception as exc:
            print(f"[Watchdog Loop Exception] {exc}", flush=True)

    @bot.event
    async def on_ready() -> None:
        print(f"[CTO Sentinel v2] Online sebagai {bot.user} | Anjing Penjaga Siap Melayani CTO!", flush=True)
        if not watchdog_heartbeat.is_running():
            watchdog_heartbeat.start()

    @bot.event
    async def on_command_error(ctx: Any, error: Any) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await send_split_message(
                ctx,
                f"⚠️ Parameter perintah kurang: `{error.param.name}`. Ketik `!help` untuk format lengkap.",
            )
            return

        orig_err = getattr(error, "original", error)
        print(f"[Discord Command Error] {ctx.command}: {orig_err}", flush=True)
        err_text = str(orig_err)[:1000]
        await send_split_message(
            ctx,
            f"⚠️ Terjadi kendala saat menjalankan `!{ctx.invoked_with}`:\n```{err_text}```",
        )

    @bot.command(name="help", aliases=["commands", "menu", "bantuan"])
    async def cmd_help(ctx: Any) -> None:
        """Menampilkan panduan lengkap perintah Ruang Kantor Virtual CTO & Anjing Penjaga."""
        menu = (
            "🏛️ **PAKDIR v2 — SENTINEL & CTO CONTROL PLANE DAFTAR PERINTAH**\n"
            "Gunakan prefix `!` di awal setiap perintah.\n\n"
            "**1. 🐕 Pemantauan Host & Anjing Penjaga (SysAdmin)**\n"
            "• `!status` : Dashboard real-time kesehatan host, alert aktif, dan sprint/task.\n"
            "• `!top [limit]` : Tampilkan proses pemakan RAM terbesar di server (default 5).\n"
            "• `!logs <service> [lines]` : Baca log journalctl service systemd (misal: `!logs caddy 30`).\n"
            "• `!investigate <service|issue>` : Investigasi akar masalah (RCA) service atau error log terlampir.\n"
            "• `!restart <service>` : Restart aman layanan systemd dengan verifikasi otomatis pasca-restart.\n"
            "• `!clean` : Bersihkan journald log usang, temporary files, dan kompaksi database.\n"
            "• `!health` : Dashboard kesehatan sistem (RAM, Swap, Disk, Layanan, Git).\n"
            "• `!security` (atau `!sec`) : Audit keamanan server & status password portal arsitektur (Port 8080).\n"
            "• `!ports` : Cek port aktif/listening (`ss -tuln`) dan status firewall UFW.\n"
            "• `!exec <command>` : Jalankan perintah shell langsung di host Linux dari Discord.\n\n"
            "**2. 🛡️ Safe Hotfix & Delivery Gatekeeper**\n"
            "• `!hotfix <instruksi>` : Buat branch hotfix, aplikasikan patch terarah, dan siapkan approval.\n"
            "• `!pending` : Lihat daftar rilis/hotfix yang menunggu persetujuan (approval) CTO.\n"
            "• `!approve <task_id> [catatan]` : Otorisasi deploy & merge kode ke branch `main`.\n"
            "• `!reject <task_id> <alasan>` : Tolak task/hotfix dan simpan catatan revisi.\n"
            "• `!retry <task_id> [catatan]` : Lanjutkan atau revisi task/hotfix.\n"
            "• `!taskinfo <task_id>` : Periksa bukti hasil kerja dan deliverable lengkap.\n\n"
            "**3. 💬 Konsultasi & Manajemen Tim AI**\n"
            "• `!ask <@agent> [pertanyaan]` : Tanya/konsultasi langsung ke engineer AI.\n"
            "  *(Mendukung lampiran berkas .pdf, .txt, .md, .py, .json, .sql, .log)*\n"
            "• `!agents` : Tampilkan daftar seluruh karyawan AI aktif, peran, dan toolsnya.\n"
            "• `!hire <nama> <deskripsi dan SOP>` : Merekrut spesialis baru secara instan.\n"
            "• `!specialize <@agent> <fokus>` : Memperdalam/memperbarui SOP karyawan AI."
        )
        await send_split_message(ctx, menu)

    @bot.command(name="health")
    async def cmd_health(ctx: Any) -> None:
        await send_split_message(ctx, get_server_health_report())

    @bot.command(name="security", aliases=["sec", "audit"])
    async def cmd_security(ctx: Any) -> None:
        """Memeriksa postur keamanan host dan status proteksi password portal."""
        report = await asyncio.to_thread(get_security_audit_report)
        await send_split_message(ctx, report)

    @bot.command(name="ports")
    async def cmd_ports(ctx: Any) -> None:
        """Cek port listening & firewall secara langsung tanpa menunggu LLM."""
        ports = get_listening_ports()
        fw = get_firewall_status()
        report = (
            "🔌 **Status Port & Jaringan Server (Live)**:\n"
            f"```\n{ports}\n```\n"
            f"🛡️ **Firewall (UFW)**:\n`{fw}`"
        )
        await send_split_message(ctx, report)

    @bot.command(name="exec")
    async def cmd_exec(ctx: Any, *, command: str) -> None:
        """Menjalankan perintah terminal di host langsung atas instruksi CTO."""
        clean_cmd = command.strip().strip("`")
        display_cmd = clean_cmd[:100] + "..." if len(clean_cmd) > 100 else clean_cmd
        print(f"[Discord Exec] Instruksi terminal dari CTO: '{clean_cmd}'", flush=True)
        await ctx.send(f"⚙️ Mengeksekusi di server: `{display_cmd}`...")
        out = await asyncio.to_thread(execute_server_command, clean_cmd)
        msg = f"💻 **Hasil Eksekusi `{display_cmd}`**:\n```bash\n{out}\n```"
        await send_split_message(ctx, msg)

    @bot.command(name="top")
    async def cmd_top(ctx: Any, limit: int = 5) -> None:
        """Melihat proses pemakan RAM terbesar di server."""
        lim = max(1, min(limit, 20))
        report = await asyncio.to_thread(handler.handle_top, lim)
        await send_split_message(ctx, report)

    @bot.command(name="logs")
    async def cmd_logs(ctx: Any, service_name: str, lines: int = 40) -> None:
        """Melihat log journalctl dari suatu service systemd."""
        cnt = max(10, min(lines, 200))
        report = await asyncio.to_thread(handler.handle_logs, service_name, cnt)
        await send_split_message(ctx, report)

    @bot.command(name="investigate", aliases=["triage", "rca", "diagnose"])
    async def cmd_investigate(ctx: Any, target_or_svc: Optional[str] = None) -> None:
        """Melakukan Root Cause Analysis (RCA) mendalam dari service atau file log terlampir."""
        uploads_dir = handler.root_dir / "data" / "uploads"
        attachments_context = ""

        if ctx.message.attachments:
            processed_items = []
            for att in ctx.message.attachments:
                await ctx.send(f"📎 Mengunduh berkas log/diagnostik: `{att.filename}`...")
                raw_bytes = await att.read()
                res = await asyncio.to_thread(
                    process_single_attachment,
                    att.filename,
                    raw_bytes,
                    uploads_dir,
                )
                processed_items.append(res)
            attachments_context = format_attachments_context(processed_items)

        if attachments_context:
            await ctx.send("🔍 **[@investigator]** Membaca berkas log terlampir dan menganalisis akar masalah...")
            report = await asyncio.to_thread(
                handler.handle_investigate_log,
                attachments_context,
                f"Attachment ({ctx.message.attachments[0].filename})",
            )
            await send_split_message(ctx, report)
            return

        target = (target_or_svc or "").strip()
        if not target:
            await ctx.send("⚠️ Masukkan nama service yang ingin diinvestigasi atau lampirkan berkas log. Contoh: `!investigate caddy`")
            return

        await ctx.send(f"🔍 **[@investigator]** Mengambil log journalctl untuk `{target}` dan melakukan RCA...")
        try:
            report = await asyncio.to_thread(handler.handle_investigate_service, target, 50)
            await send_split_message(ctx, report)
        except Exception as e:
            await send_split_message(ctx, f"⚠️ Gagal melakukan investigasi: {e}")

    @bot.command(name="restart")
    async def cmd_restart(ctx: Any, service_name: str) -> None:
        """Me-restart layanan systemd secara aman."""
        await ctx.send(f"⚙️ Me-restart layanan systemd: `{service_name}`...")
        res = await asyncio.to_thread(handler.handle_restart, service_name)
        await send_split_message(ctx, res)

    @bot.command(name="clean")
    async def cmd_clean(ctx: Any) -> None:
        """Membersihkan log journal usang, temporary uploads, dan kompaksi database."""
        await ctx.send("🧹 Membersihkan berkas sementara, journalctl lama, dan mengompresi database...")
        res = await asyncio.to_thread(handler.handle_clean)
        await send_split_message(ctx, res)

    @bot.command(name="hotfix")
    async def cmd_hotfix(ctx: Any, *, instruction: str) -> None:
        """Membuat branch hotfix, menerapkan perbaikan terarah, dan menyiapkan approval gate."""
        if not instruction.strip():
            await ctx.send("⚠️ Sertakan deskripsi hotfix. Contoh: `!hotfix ubah port timeout caddy ke 60 detik`")
            return

        loop = asyncio.get_running_loop()

        def send_progress(msg: str) -> None:
            asyncio.run_coroutine_threadsafe(send_split_message(ctx, msg), loop)

        try:
            await asyncio.to_thread(handler.handle_hotfix, instruction, send_progress)
        except Exception as exc:
            err_msg = f"⚠️ Eksekusi hotfix gagal:\n```{str(exc)[:1500]}```"
            await send_split_message(ctx, err_msg)

    @bot.command(name="agents")
    async def cmd_agents(ctx: Any) -> None:
        await send_split_message(ctx, handler.handle_agents_list())

    @bot.command(name="ask")
    async def cmd_ask(ctx: Any, agent_name: str, *, question: Optional[str] = None) -> None:
        clean_agent = agent_name.lstrip("@").lower()
        uploads_dir = handler.root_dir / "data" / "uploads"

        # 1. Unduh dan proses jika ada lampiran file (PDF, TXT, PY, JSON, SQL, dll.)
        attachments_context = ""
        if ctx.message.attachments:
            processed_items = []
            for att in ctx.message.attachments:
                await ctx.send(f"📎 Mengunduh & memproses lampiran: `{att.filename}` ({att.size / 1024:.1f} KB)...")
                raw_bytes = await att.read()
                res = await asyncio.to_thread(
                    process_single_attachment,
                    att.filename,
                    raw_bytes,
                    uploads_dir,
                )
                processed_items.append(res)
            attachments_context = format_attachments_context(processed_items)

        # 2. Tangani pertanyaan/prompt
        user_question = (question or "").strip()
        if not user_question:
            if attachments_context:
                user_question = "Tolong analisa, review, dan jelaskan poin-poin penting serta rekomendasi teknis dari dokumen/berkas terlampir."
            else:
                await ctx.send(
                    f"⚠️ Silakan sertakan pertanyaan atau lampiran berkas untuk `@{clean_agent}`.\n"
                    f"Contoh: `!ask {clean_agent} apa fungsi modul ini?` atau unggah berkas dengan `!ask {clean_agent}`"
                )
                return

        final_prompt = f"{user_question}\n\n{attachments_context}" if attachments_context else user_question

        num_att = len(ctx.message.attachments) if ctx.message.attachments else 0
        print(f"[Discord] Diterima perintah: !ask @{clean_agent} (lampiran: {num_att}) '{user_question[:80]}'", flush=True)
        await ctx.send(f"🔍 Menghubungi `@{clean_agent}`...")
        try:
            reply = await asyncio.to_thread(handler.handle_ask, clean_agent, final_prompt)
            await send_split_message(ctx, reply)
        except Exception as exc:
            print(f"[Discord Error] {exc}", flush=True)
            err_msg = f"⚠️ Terjadi kendala saat memanggil `@{clean_agent}`:\n```{str(exc)[:1500]}```"
            await send_split_message(ctx, err_msg)

    @bot.command(name="hire")
    async def cmd_hire(ctx: Any, name: str, *, spec: str) -> None:
        reply = await asyncio.to_thread(
            handler.handle_hire,
            name=name,
            title=f"Specialist ({name})",
            description=spec[:100],
            sop=spec,
            tools="write,edit,bash",
        )
        await send_split_message(ctx, reply)

    @bot.command(name="specialize")
    async def cmd_specialize(ctx: Any, name: str, *, focus: str) -> None:
        reply = await asyncio.to_thread(handler.handle_specialize, name.lstrip("@"), focus)
        await send_split_message(ctx, reply)
    @bot.command(name="status", aliases=["tasks", "antrean", "antrian", "progress"])
    async def cmd_status(ctx: Any) -> None:
        """Memeriksa status real-time agen, task aktif, dan antrean."""
        reply = await asyncio.to_thread(handler.handle_status)
        await send_split_message(ctx, reply)

    @bot.command(name="taskinfo", aliases=["detail", "taskdetail", "info", "inspect"])
    async def cmd_taskinfo(ctx: Any, task_id: int) -> None:
        """Memeriksa bukti kerja lengkap (PRD, Arsitektur, Kode, QA) dari suatu task."""
        reply = await asyncio.to_thread(handler.handle_taskinfo, task_id)
        await send_split_message(ctx, reply)

    @bot.command(name="activity", aliases=["events", "log", "timeline", "kegiatan"])
    async def cmd_activity(ctx: Any, limit: int = 10) -> None:
        """Melihat riwayat timeline aktivitas agen terkini."""
        reply = await asyncio.to_thread(handler.handle_activity, limit)
        await send_split_message(ctx, reply)

    @bot.command(name="pending")
    async def cmd_pending(ctx: Any) -> None:
        await send_split_message(ctx, handler.handle_pending())

    @bot.command(name="approve")
    async def cmd_approve(ctx: Any, task_id: int, *, note: str = "Approved by CTO via Discord") -> None:
        try:
            task = await asyncio.to_thread(handler.supervisor.approve_task, task_id, note)
            commit = task.get("commit_hash", "N/A")
            branch = task.get("branch_name", "feat/...")
            files = task.get("merged_files", [])
            files_str = "\n".join(f"  • `{f}`" for f in files[:8]) if files else "  • *(Arsip deliverable di docs/)*"

            msg = (
                f"🚀 **Task #{task_id} Resmi Approved & Deployed ke Produksi!**\n"
                f"• **Branch:** `{branch}` ➔ `main`\n"
                f"• **Git Commit:** `{commit}`\n"
                f"• **Catatan CTO:** *{note}*\n"
                f"• **Berkas yang Di-merge:**\n{files_str}\n\n"
                f"💡 *Ketik `!taskinfo {task_id}` untuk melihat bukti deliverable lengkap, atau `!exec git log -1 --stat` untuk cek status git di server.*"
            )
            await send_split_message(ctx, msg)
        except Exception as exc:
            err_msg = f"⚠️ Gagal approve task #{task_id}: {str(exc)[:1500]}"
            await send_split_message(ctx, err_msg)

    @bot.command(name="reject")
    async def cmd_reject(ctx: Any, task_id: int, *, reason: str) -> None:
        await asyncio.to_thread(handler.supervisor.reject_task, task_id, reason)
        msg = (
            f"🛑 **Task #{task_id} Ditolak CTO.** Catatan revisi disimpan: *{reason}*\n"
            f"💡 *Ketik `!retry {task_id}` untuk langsung memerintahkan tim AI merevisi kode sesuai catatan ini.*"
        )
        await send_split_message(ctx, msg)

    @bot.command(name="retry", aliases=["resume", "lanjut", "revisi"])
    async def cmd_retry(ctx: Any, *, args: Optional[str] = None) -> None:
        """
        Melanjutkan pengerjaan task yang berhenti atau merevisi task yang ditolak.
        Mendukung berbagai format pemanggilan:
        1. !retry (otomatis mengambil task terakhir yang aktif/berhenti)
        2. !retry <task_id> (misal: !retry 1)
        3. !retry <task_id> <catatan revisi> (misal: !retry 1 tolong perbaiki navbar)
        4. !retry <catatan revisi> (misal: !retry perbaiki tampilan tombol)
        """
        raw_args = (args or "").strip()
        task_id: Optional[int] = None
        note: str = ""

        if raw_args:
            parts = raw_args.split(maxsplit=1)
            if parts[0].isdigit():
                task_id = int(parts[0])
                note = parts[1].strip() if len(parts) > 1 else ""
            else:
                note = raw_args

        if task_id is None:
            active = handler.state_db.get_active_tasks()
            if active:
                task_id = int(active[0]["id"])
            else:
                recent = handler.state_db.get_recent_tasks(limit=1)
                if recent:
                    task_id = int(recent[0]["id"])

        if task_id is None:
            await send_split_message(
                ctx,
                "⚠️ Tidak ada task/hotfix yang ditemukan di sistem. Silakan buat perbaikan baru dengan `!hotfix <instruksi>`.",
            )
            return

        loop = asyncio.get_running_loop()

        def send_progress(msg: str) -> None:
            asyncio.run_coroutine_threadsafe(send_split_message(ctx, msg), loop)

        try:
            target_task = handler.state_db.get_task(task_id)
            title = target_task.get("title", f"#{task_id}") if target_task else f"#{task_id}"
            await ctx.send(f"🔄 Mengaktifkan kembali tim AI untuk melanjutkan Task #{task_id}: *{title}*...")
            await asyncio.to_thread(
                handler.supervisor.resume_task,
                task_id,
                note,
                send_progress,
            )
        except Exception as exc:
            print(f"[Discord Error Retry] {exc}", flush=True)
            err_msg = f"⚠️ Gagal melanjutkan task #{task_id}:\n```{str(exc)[:1500]}```"
            await send_split_message(ctx, err_msg)

    bot.run(token)


if __name__ == "__main__":
    run_discord_bot()
