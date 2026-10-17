import asyncio
import os
import platform
import shutil
from pathlib import Path
from typing import Dict, Any

from orchestrator.agent_factory import AgentFactory
from orchestrator.llm_router import FreeLLMRouter, FREE_MODEL_POOLS
from orchestrator.state_db import StateDB
from orchestrator.supervisor import EMSupervisor


def get_server_health_report() -> str:
    """
    Mengambil metrik kesehatan server (RAM, Swap, Disk, OS) secara ringan.
    Kompatibel di Linux maupun Windows / macOS.
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

    return (
        "🖥️ **Status Telemetri & Kesehatan Node CTO**\n"
        f"- **OS / Platform:** `{platform.system()} {platform.release()}`\n"
        f"- **Pemakaian RAM:** `{ram_info}`\n"
        f"- **Pemakaian Swap:** `{swap_info}`\n"
        f"- **Pemakaian Disk:** `{disk_info}`\n"
        f"- **Pool Reasoning Models:** `{', '.join(FREE_MODEL_POOLS['reasoning'][:3])}...`\n"
        f"- **Pool Coding Models:** `{', '.join(FREE_MODEL_POOLS['coding'][:3])}...`"
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
    bot = commands.Bot(command_prefix="!", intents=intents)
    handler = CTOCommandHandler(".")

    @bot.event
    async def on_ready() -> None:
        print(f"[CTO Orchestrator] Online sebagai {bot.user} | Siap melayani CTO!", flush=True)

    @bot.command(name="health")
    async def cmd_health(ctx: Any) -> None:
        await ctx.send(get_server_health_report())

    @bot.command(name="agents")
    async def cmd_agents(ctx: Any) -> None:
        await ctx.send(handler.handle_agents_list())

    @bot.command(name="ask")
    async def cmd_ask(ctx: Any, agent_name: str, *, question: str) -> None:
        clean_agent = agent_name.lstrip("@").lower()
        print(f"[Discord] Diterima perintah: !ask @{clean_agent} '{question}'", flush=True)
        await ctx.send(f"🔍 Menghubungi `@{clean_agent}`...")
        try:
            reply = await asyncio.to_thread(handler.handle_ask, clean_agent, question)
            await ctx.send(reply[:1950])
        except Exception as exc:
            print(f"[Discord Error] {exc}", flush=True)
            await ctx.send(f"⚠️ Terjadi kendala saat memanggil `@{clean_agent}`:\n```{exc}```")

    @bot.command(name="hire")
    async def cmd_hire(ctx: Any, name: str, *, spec: str) -> None:
        # Format cepat: !hire <nama> <deskripsi dan SOP>
        reply = await asyncio.to_thread(
            handler.handle_hire,
            name=name,
            title=f"Specialist ({name})",
            description=spec[:100],
            sop=spec,
            tools="write,edit,bash",
        )
        await ctx.send(reply)

    @bot.command(name="specialize")
    async def cmd_specialize(ctx: Any, name: str, *, focus: str) -> None:
        reply = await asyncio.to_thread(handler.handle_specialize, name.lstrip("@"), focus)
        await ctx.send(reply)

    @bot.command(name="task")
    async def cmd_task(ctx: Any, *, instruction: str) -> None:
        loop = asyncio.get_running_loop()

        def send_progress(msg: str) -> None:
            asyncio.run_coroutine_threadsafe(ctx.send(msg[:1950]), loop)

        try:
            await asyncio.to_thread(handler.supervisor.execute_cto_mandate, instruction, send_progress)
        except Exception as exc:
            print(f"[Discord Error Task] {exc}")
            await ctx.send(f"⚠️ Eksekusi task gagal:\n```{exc}```")

    @bot.command(name="pending")
    async def cmd_pending(ctx: Any) -> None:
        await ctx.send(handler.handle_pending())

    @bot.command(name="approve")
    async def cmd_approve(ctx: Any, task_id: int, *, note: str = "Approved by CTO via Discord") -> None:
        task = await asyncio.to_thread(handler.supervisor.approve_task, task_id, note)
        await ctx.send(
            f"🚀 **Task #{task_id} Approved & Deployed!**\n"
            f"Branch `{task['branch_name']}` telah di-merge ke `main`."
        )

    @bot.command(name="reject")
    async def cmd_reject(ctx: Any, task_id: int, *, reason: str) -> None:
        await asyncio.to_thread(handler.supervisor.reject_task, task_id, reason)
        await ctx.send(f"🛑 **Task #{task_id} Ditolak CTO.** Catatan revisi disimpan: *{reason}*")

    bot.run(token)


if __name__ == "__main__":
    run_discord_bot()
