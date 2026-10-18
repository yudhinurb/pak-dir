import argparse
import sys
from orchestrator.discord_bot import CTOCommandHandler, get_server_health_report

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def print_banner() -> None:
    print(
        "\n"
        "======================================================================\n"
        "🏢 PakDir — CTO Interactive Local Console\n"
        "Kelola organisasi karyawan AI langsung dari terminal lokal.\n"
        "Ketik 'help' untuk daftar perintah, atau 'exit' untuk keluar.\n"
        "======================================================================\n"
    )


def interactive_repl() -> None:
    handler = CTOCommandHandler(".")
    print_banner()

    while True:
        try:
            line = input("PakDir CTO > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nSampai jumpa, CTO!")
            break

        if not line:
            continue
        if line.lower() in ("exit", "quit"):
            print("Keluar dari konsol CTO. Sampai jumpa!")
            break
        if line.lower() in ("help", "?"):
            print(
                "\nPerintah yang Tersedia:\n"
                "  !agents                 - Tampilkan daftar karyawan AI aktif\n"
                "  !status                 - Cek status real-time agen dan task aktif\n"
                "  !ask <agent> <tanya>    - Tanya langsung ke agen tertentu (misal: !ask architect ...)\n"
                "  !task <instruksi>       - Berikan inisiatif fitur ke @em (Full Pipeline)\n"
                "  !pending                - Lihat daftar task yang menunggu approval CTO\n"
                "  !approve <id> [catatan] - Setujui deployment task\n"
                "  !reject <id> <alasan>   - Tolak task & minta revisi\n"
                "  !retry [id] [catatan]   - Lanjutkan atau revisi task yang berhenti/ditolak\n"
                "  !hire <nama> <sop>      - Rekrut agen spesialis baru\n"
                "  !specialize <nama> <fokus> - Pertajam SOP agen yang ada\n"
                "  !health                 - Cek telemetri kesehatan host\n"
                "  exit                    - Keluar\n"
            )
            continue

        cmd = line.split()[0]
        args = line[len(cmd) :].strip()

        if cmd == "!agents":
            print(handler.handle_agents_list())
        elif cmd == "!status":
            print(handler.handle_status())
        elif cmd == "!top":
            lim = int(args) if args.isdigit() else 5
            print(handler.handle_top(lim))
        elif cmd == "!logs":
            parts = args.split()
            svc = parts[0] if parts else "caddy"
            cnt = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 40
            print(handler.handle_logs(svc, cnt))
        elif cmd == "!investigate":
            if not args:
                print("Format: !investigate <nama_service>")
                continue
            print(f"🔍 Menginvestigasi service {args}...")
            print(handler.handle_investigate_service(args))
        elif cmd == "!restart":
            if not args:
                print("Format: !restart <nama_service>")
                continue
            print(handler.handle_restart(args))
        elif cmd == "!clean":
            print(handler.handle_clean())
        elif cmd == "!hotfix":
            if not args:
                print("Format: !hotfix <deskripsi perbaikan>")
                continue
            print("🛡️ Menyiapkan hotfix terarah...")
            res = handler.handle_hotfix(args, progress_callback=print)
            print(f"\nStatus Hotfix #{res.get('id')}: {res.get('status')}")
        elif cmd == "!health":
            print(get_server_health_report())
        elif cmd == "!pending":
            print(handler.handle_pending())
        elif cmd == "!ask":
            parts = args.split(maxsplit=1)
            if len(parts) < 2:
                print("Format: !ask <nama_agent> <pertanyaan>")
                continue
            print(handler.handle_ask(parts[0], parts[1]))
        elif cmd == "!hire":
            parts = args.split(maxsplit=1)
            if len(parts) < 2:
                print("Format: !hire <nama_agent> <deskripsi dan aturan SOP>")
                continue
            name = parts[0]
            sop = parts[1]
            print(
                handler.handle_hire(
                    name=name,
                    title=f"Specialist ({name})",
                    description=sop[:100],
                    sop=sop,
                    tools="write,edit,bash",
                )
            )
        elif cmd == "!specialize":
            parts = args.split(maxsplit=1)
            if len(parts) < 2:
                print("Format: !specialize <nama_agent> <fokus_baru>")
                continue
            print(handler.handle_specialize(parts[0], parts[1]))
        elif cmd == "!task":
            if not args:
                print("Format: !task <instruksi lengkap>")
                continue
            print("🚀 Menjalankan inisiatif di bawah pengawasan @em...")
            res = handler.supervisor.execute_cto_mandate(args, progress_callback=print)
            print(f"\nStatus Terakhir Task #{res.get('id')}: {res.get('status')}")
        elif cmd == "!approve":
            parts = args.split(maxsplit=1)
            if not parts:
                print("Format: !approve <task_id> [catatan]")
                continue
            try:
                task_id = int(parts[0])
            except ValueError:
                print("Task ID harus berupa angka!")
                continue
            note = parts[1] if len(parts) > 1 else "Approved by CTO via Local Console"
            try:
                res = handler.supervisor.approve_task(task_id, note)
                print(f"✅ Berhasil! Task #{task_id} status: {res.get('status')}")
            except Exception as e:
                print(f"❌ Error: {e}")
        elif cmd == "!reject":
            parts = args.split(maxsplit=1)
            if len(parts) < 2:
                print("Format: !reject <task_id> <alasan>")
                continue
            try:
                task_id = int(parts[0])
            except ValueError:
                print("Task ID harus berupa angka!")
                continue
            try:
                res = handler.supervisor.reject_task(task_id, parts[1])
                print(f"🛑 Task #{task_id} ditolak: {res.get('status')}")
            except Exception as e:
                print(f"❌ Error: {e}")
        elif cmd in ("!retry", "!resume", "!lanjut", "!revisi"):
            task_id = None
            note = ""
            if args:
                parts = args.split(maxsplit=1)
                if parts[0].isdigit():
                    task_id = int(parts[0])
                    note = parts[1].strip() if len(parts) > 1 else ""
                else:
                    note = args
            if task_id is None:
                active = handler.state_db.get_active_tasks()
                if active:
                    task_id = int(active[0]["id"])
                else:
                    recent = handler.state_db.get_recent_tasks(limit=1)
                    if recent:
                        task_id = int(recent[0]["id"])
            if task_id is None:
                print("⚠️ Tidak ada task yang ditemukan untuk di-retry.")
                continue
            print(f"🔄 Melanjutkan Task #{task_id}...")
            try:
                res = handler.supervisor.resume_task(task_id, note, progress_callback=print)
                print(f"\nStatus Terakhir Task #{task_id}: {res.get('status')}")
            except Exception as e:
                print(f"❌ Error: {e}")
        elif cmd in ("!status", "!tasks"):
            print(handler.handle_status())
        else:
            print(f"Perintah '{cmd}' tidak dikenali. Ketik 'help' untuk daftar perintah.")


def main() -> None:
    parser = argparse.ArgumentParser(description="PakDir CTO Local CLI")
    sub = parser.add_subparsers(dest="subcommand")

    sub.add_parser("interactive", help="Buka shell interaktif CTO")
    sub.add_parser("agents", help="Daftar karyawan AI aktif")
    sub.add_parser("health", help="Telemetri kesehatan node")
    sub.add_parser("pending", help="Daftar task menunggu approval")
    sub.add_parser("status", help="Status real-time tugas dan tim AI")

    ask_p = sub.add_parser("ask", help="Tanya langsung ke agen")
    ask_p.add_argument("agent", help="Nama agen (misal: architect)")
    ask_p.add_argument("question", help="Pertanyaan teknis")

    task_p = sub.add_parser("task", help="Eksekusi inisiatif fitur baru")
    task_p.add_argument("instruction", help="Instruksi fitur")

    app_p = sub.add_parser("approve", help="Approve task")
    app_p.add_argument("task_id", type=int, help="ID Task")
    app_p.add_argument("--note", default="Approved by CTO", help="Catatan persetujuan")

    retry_p = sub.add_parser("retry", help="Lanjutkan atau revisi task yang berhenti/ditolak")
    retry_p.add_argument("task_id", type=int, nargs="?", default=None, help="ID Task (opsional)")
    retry_p.add_argument("--note", default="", help="Catatan revisi / arahan baru")

    args = parser.parse_args()
    handler = CTOCommandHandler(".")

    if args.subcommand == "interactive" or len(sys.argv) == 1:
        interactive_repl()
    elif args.subcommand == "agents":
        print(handler.handle_agents_list())
    elif args.subcommand == "health":
        print(get_server_health_report())
    elif args.subcommand == "pending":
        print(handler.handle_pending())
    elif args.subcommand == "status":
        print(handler.handle_status())
    elif args.subcommand == "ask":
        print(handler.handle_ask(args.agent, args.question))
    elif args.subcommand == "task":
        handler.supervisor.execute_cto_mandate(args.instruction, progress_callback=print)
    elif args.subcommand == "approve":
        print(handler.supervisor.approve_task(args.task_id, args.note))
    elif args.subcommand == "retry":
        task_id = args.task_id
        if task_id is None:
            active = handler.state_db.get_active_tasks()
            task_id = int(active[0]["id"]) if active else 1
        res = handler.supervisor.resume_task(task_id, args.note, progress_callback=print)
        print(f"\nStatus Terakhir Task #{task_id}: {res.get('status')}")


if __name__ == "__main__":
    main()
