import asyncio
import os
import platform
import shutil
from pathlib import Path
from typing import Dict, Any, List

from orchestrator.agent_factory import AgentFactory
from orchestrator.llm_router import FreeLLMRouter, FREE_MODEL_POOLS
from orchestrator.state_db import StateDB
from orchestrator.supervisor import EMSupervisor
from orchestrator.system_context import (
    get_listening_ports,
    get_firewall_status,
    get_active_services,
    get_git_status,
    execute_server_command,
)
from orchestrator.file_processor import (
    process_single_attachment,
    format_attachments_context,
)


async def send_split_message(ctx: Any, text: str, max_chunk_size: int = 1850) -> None:
    """
    Mengirim pesan ke Discord dengan memecah pesan panjang menjadi beberapa bagian
    agar tidak terpotong oleh batas limit 2000 karakter Discord dan memastikan
    blok kode Markdown (```) tidak rusak di tengah.
    """
    if not text:
        return

    text = str(text).strip()
    if len(text) <= max_chunk_size:
        await ctx.send(text)
        return

    chunks: List[str] = []
    remaining = text

    while len(remaining) > max_chunk_size:
        # Cari titik pemisah terbaik di batas baris baru
        split_idx = remaining.rfind("\n", 0, max_chunk_size)
        if split_idx == -1 or split_idx < max_chunk_size // 3:
            # Jika tidak ada baris baru yang pas, cari spasi
            split_idx = remaining.rfind(" ", 0, max_chunk_size)
        if split_idx == -1:
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
            await ctx.send(header + c)
        else:
            await ctx.send(c)


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

    def __init__(self, root_dir: str | Path = ".") -> None:
        self.root_dir = Path(root_dir).resolve()
        self.state_db = StateDB(self.root_dir / "data" / "company_state.db")
        self.router = FreeLLMRouter(state_db=self.state_db)
        self.factory = AgentFactory(root_dir=self.root_dir, state_db=self.state_db)
        self.supervisor = EMSupervisor(
            root_dir=self.root_dir,
            state_db=self.state_db,
            router=self.router,
            factory=self.factory,
        )

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


def run_discord_bot() -> None:
    """
    Menjalankan Discord Bot untuk Ruang Kantor Virtual CTO.
    """
    try:
        import discord
        from discord.ext import commands
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

    @bot.event
    async def on_ready() -> None:
        print(f"[CTO Orchestrator] Online sebagai {bot.user} | Siap melayani CTO!", flush=True)

    @bot.command(name="help", aliases=["commands", "menu", "bantuan"])
    async def cmd_help(ctx: Any) -> None:
        """Menampilkan panduan lengkap perintah Ruang Kantor Virtual CTO."""
        menu = (
            "🏛️ **KANTOR VIRTUAL CTO — DAFTAR PERINTAH RESMI (PakDir)**\n"
            "Gunakan prefix `!` di awal setiap perintah.\n\n"
            "**1. Konsultasi & Manajemen Tim AI**\n"
            "• `!ask <@agent> [pertanyaan]` : Tanya/konsultasi langsung ke engineer AI.\n"
            "  *(Mendukung lampiran file! Unggah berkas .pdf, .txt, .md, .py, .json, .sql, .log untuk dianalisis)*\n"
            "  *Contoh:* `!ask architect port apa aja yang terekspos di server?`\n"
            "  *Contoh:* `!ask architect` + lampirkan `architecture_spec.pdf`\n"
            "• `!agents` : Tampilkan daftar seluruh karyawan AI aktif, peran, dan toolsnya.\n"
            "• `!hire <nama> <deskripsi dan SOP>` : Merekrut karyawan AI spesialis baru secara instan.\n"
            "  *Contoh:* `!hire sec-expert Audit celah keamanan SQLi dan JWT`\n"
            "• `!specialize <@agent> <fokus>` : Memperdalam/memperbarui SOP karyawan AI.\n"
            "  *Contoh:* `!specialize dev Gunakan Go Fiber dan SQLite WAL mode`\n\n"
            "**2. Eksekusi Mandat & Persetujuan Produksi**\n"
            "• `!task [instruksi]` : Jalankan sprint otomatis end-to-end (PM ➔ Architect ➔ Dev ➔ Ops ➔ Auto-Healing).\n"
            "  *(Bisa melampirkan berkas PRD / spesifikasi .pdf / .md sebagai acuan sprint)*\n"
            "  *Contoh:* `!task Bangun endpoint REST API /api/health dengan status database`\n"
            "• `!pending` : Lihat daftar task/PR yang menunggu persetujuan (approval) CTO.\n"
            "• `!approve <task_id> [catatan]` : Otorisasi deploy & merge kode ke branch `main`.\n"
            "• `!reject <task_id> <alasan>` : Tolak task dan instruksikan tim AI untuk revisi.\n\n"
            "**3. Telemetri & Kontrol Server Host (DevOps)**\n"
            "• `!health` : Dashboard kesehatan sistem (RAM, Swap, Disk, Layanan Inti, Git).\n"
            "• `!ports` : Cek port aktif/listening (`ss -tuln`) dan status firewall UFW.\n"
            "• `!exec <command>` : Jalankan perintah shell langsung di host Linux dari Discord.\n"
            "  *Contoh:* `!exec df -h` atau `!exec git log -3 --oneline`\n"
            "• `!help` (atau `!commands` / `!menu`) : Tampilkan panduan menu ini."
        )
        await send_split_message(ctx, menu)

    @bot.command(name="health")
    async def cmd_health(ctx: Any) -> None:
        await send_split_message(ctx, get_server_health_report())

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
        print(f"[Discord Exec] Instruksi terminal dari CTO: '{clean_cmd}'", flush=True)
        await ctx.send(f"⚙️ Mengeksekusi di server: `{clean_cmd}`...")
        out = await asyncio.to_thread(execute_server_command, clean_cmd)
        msg = f"💻 **Hasil Eksekusi `{clean_cmd}`**:\n```bash\n{out}\n```"
        await send_split_message(ctx, msg)

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
            await ctx.send(f"⚠️ Terjadi kendala saat memanggil `@{clean_agent}`:\n```{exc}```")

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

    @bot.command(name="task")
    async def cmd_task(ctx: Any, *, instruction: Optional[str] = None) -> None:
        uploads_dir = handler.root_dir / "data" / "uploads"

        # 1. Unduh dan proses jika ada lampiran file mandat/spesifikasi
        attachments_context = ""
        if ctx.message.attachments:
            processed_items = []
            for att in ctx.message.attachments:
                await ctx.send(f"📎 Mengunduh dokumen mandat: `{att.filename}` ({att.size / 1024:.1f} KB)...")
                raw_bytes = await att.read()
                res = await asyncio.to_thread(
                    process_single_attachment,
                    att.filename,
                    raw_bytes,
                    uploads_dir,
                )
                processed_items.append(res)
            attachments_context = format_attachments_context(processed_items)

        task_instruction = (instruction or "").strip()
        if not task_instruction:
            if attachments_context:
                task_instruction = "Implementasikan fitur dan rancangan sistem sesuai dokumen spesifikasi terlampir."
            else:
                await ctx.send("⚠️ Silakan sertakan instruksi atau lampirkan dokumen spesifikasi tugas. Contoh: `!task Buat endpoint /api/health`")
                return

        final_instruction = f"{task_instruction}\n\n{attachments_context}" if attachments_context else task_instruction

        loop = asyncio.get_running_loop()

        def send_progress(msg: str) -> None:
            asyncio.run_coroutine_threadsafe(send_split_message(ctx, msg), loop)

        try:
            await asyncio.to_thread(handler.supervisor.execute_cto_mandate, final_instruction, send_progress)
        except Exception as exc:
            print(f"[Discord Error Task] {exc}", flush=True)
            await ctx.send(f"⚠️ Eksekusi task gagal:\n```{exc}```")

    @bot.command(name="pending")
    async def cmd_pending(ctx: Any) -> None:
        await send_split_message(ctx, handler.handle_pending())

    @bot.command(name="approve")
    async def cmd_approve(ctx: Any, task_id: int, *, note: str = "Approved by CTO via Discord") -> None:
        task = await asyncio.to_thread(handler.supervisor.approve_task, task_id, note)
        await send_split_message(
            ctx,
            f"🚀 **Task #{task_id} Approved & Deployed!**\n"
            f"Branch `{task['branch_name']}` telah di-merge ke `main`."
        )

    @bot.command(name="reject")
    async def cmd_reject(ctx: Any, task_id: int, *, reason: str) -> None:
        await asyncio.to_thread(handler.supervisor.reject_task, task_id, reason)
        await send_split_message(ctx, f"🛑 **Task #{task_id} Ditolak CTO.** Catatan revisi disimpan: *{reason}*")

    bot.run(token)


if __name__ == "__main__":
    run_discord_bot()
