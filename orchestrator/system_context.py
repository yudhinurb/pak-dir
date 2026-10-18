import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple


def get_listening_ports() -> str:
    """
    Mengambil daftar port yang sedang LISTEN di server secara instan.
    Mendukung Linux (ss/netstat) dan Windows (netstat).
    """
    if shutil.which("ss"):
        try:
            res = subprocess.run(
                ["ss", "-tuln"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                lines = res.stdout.strip().splitlines()
                # Ambil header dan entri port (batasi 20 baris agar tidak membebani)
                return "\n".join(lines[:20])
        except Exception:
            pass

    if shutil.which("netstat"):
        try:
            is_win = platform.system().lower() == "windows"
            cmd = ["netstat", "-ano"] if is_win else ["netstat", "-tuln"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                lines = [l for l in res.stdout.strip().splitlines() if "LISTEN" in l or "Proto" in l]
                if lines:
                    return "\n".join(lines[:20])
                return "\n".join(res.stdout.strip().splitlines()[:15])
        except Exception:
            pass

    return "Port listening tidak terdeteksi via ss/netstat."


def get_firewall_status() -> str:
    """
    Mengambil status firewall UFW jika tersedia di host Linux.
    """
    if shutil.which("ufw"):
        try:
            # Gunakan -n jika sudo tersedia tanpa password untuk user non-root
            res = subprocess.run(
                ["sudo", "-n", "ufw", "status"],
                capture_output=True,
                text=True,
                timeout=4,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
    return "UFW Firewall: default allow / managed outside UFW"


def get_active_services() -> str:
    """
    Mengecek status unit systemd inti: pakdir-orchestrator, caddy, ssh.
    """
    if shutil.which("systemctl"):
        svcs = ["pakdir-orchestrator", "caddy", "ssh"]
        results = []
        for s in svcs:
            try:
                res = subprocess.run(["systemctl", "is-active", s], capture_output=True, text=True, timeout=3)
                state = res.stdout.strip() or "inactive"
                results.append(f"{s}: {state}")
            except Exception:
                pass
        if results:
            return ", ".join(results)
    return "Layanan sistem dikelola langsung oleh OS."


def get_git_status() -> str:
    """
    Mengambil informasi branch git dan commit terakhir.
    """
    try:
        git_cmd = ["git", "-c", "safe.directory=*"]
        branch = subprocess.run(git_cmd + ["branch", "--show-current"], capture_output=True, text=True, timeout=3).stdout.strip()
        last_log = subprocess.run(git_cmd + ["log", "-1", "--oneline"], capture_output=True, text=True, timeout=3).stdout.strip()
        diff_stat = subprocess.run(git_cmd + ["status", "-s"], capture_output=True, text=True, timeout=3).stdout.strip()
        dirty = " (ada perubahan lokal)" if diff_stat else " (clean)"
        return f"Branch: `{branch or 'main'}` | Commit: `{last_log}`{dirty}"
    except Exception:
        return "Info Git repository tidak tersedia."


def get_live_server_context() -> str:
    """
    Snapshot telemetri server lengkap untuk diinjeksikan langsung ke System Prompt AI Router.
    """
    ports = get_listening_ports()
    services = get_active_services()
    firewall = get_firewall_status()
    git_info = get_git_summary_str()

    return (
        "=== [LIVE SERVER & HOST TELEMETRY SNAPSHOT] ===\n"
        f"- Git & Project: {git_info}\n"
        f"- Running Services: {services}\n"
        f"- Firewall: {firewall}\n"
        f"- Active Listening Ports:\n{ports}\n"
        "================================================"
    )


def get_git_summary_str() -> str:
    try:
        git_cmd = ["git", "-c", "safe.directory=*"]
        branch = subprocess.run(git_cmd + ["branch", "--show-current"], capture_output=True, text=True, timeout=3).stdout.strip()
        last_log = subprocess.run(git_cmd + ["log", "-1", "--oneline"], capture_output=True, text=True, timeout=3).stdout.strip()
        return f"{branch or 'main'} ({last_log})"
    except Exception:
        return "main"


def execute_server_command(cmd: str, timeout: int = 15) -> str:
    """
    Mengeksekusi perintah terminal atas perintah langsung CTO.
    Menyertakan safe.directory=* pada env agar tidak terkena dubious ownership error.
    """
    try:
        env = os.environ.copy()
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "safe.directory"
        env["GIT_CONFIG_VALUE_0"] = "*"

        # Jalankan di bash jika Linux, atau shell default
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        combined = out
        if err:
            combined = f"{combined}\n[STDERR]: {err}" if combined else f"[STDERR]: {err}"
        return combined or "(Perintah selesai dieksekusi tanpa output text)"
    except subprocess.TimeoutExpired:
        return f"⚠️ Perintah timeout setelah {timeout} detik."
    except Exception as e:
        return f"⚠️ Terjadi error saat mengeksekusi perintah: {e}"


def get_security_audit_report() -> str:
    """
    Melakukan audit postur keamanan host & Caddy portal architecture secara real-time.
    """
    caddy_stat = {"auth_enabled": False, "is_loopback": False, "username": None, "port": ":8080"}
    caddyfile_paths = [Path("/etc/caddy/Caddyfile"), Path("deploy/Caddyfile")]
    
    for cp in caddyfile_paths:
        if cp.exists():
            try:
                text = cp.read_text(encoding="utf-8")
                # Deteksi basicauth aktif
                match = re.search(r"(?m)^\s*basicauth\s+\*\s*\{\s*([a-zA-Z0-9_-]+)\s+(\$2[abxy]?\$[^\s]+)", text)
                if match:
                    caddy_stat["auth_enabled"] = True
                    caddy_stat["username"] = match.group(1)
                if "127.0.0.1:8080" in text or "localhost:8080" in text:
                    caddy_stat["is_loopback"] = True
                    caddy_stat["port"] = "127.0.0.1:8080"
                break
            except Exception:
                pass

    fw = get_firewall_status()
    ports = get_listening_ports()

    # Evaluasi status keamanan
    if caddy_stat["auth_enabled"]:
        portal_status = f"🟢 **TERKUNCI** (HTTP BasicAuth aktif untuk user `{caddy_stat.get('username') or 'cto'}`)"
        posture = "🛡️ **Postur Keamanan:** `EXCELLENT (Portal arsitektur aman dari pencurian/bot)`"
        mitigation = "✅ Tidak ada tindakan darurat yang diperlukan. Blueprint arsitektur dan PRD Anda aman."
    elif caddy_stat["is_loopback"]:
        portal_status = "🟢 **PRIVATE LOOPBACK** (Hanya localhost `127.0.0.1:8080` via SSH Tunnel)"
        posture = "🛡️ **Postur Keamanan:** `ZERO-TRUST (100% Private / Stealth Mode)`"
        mitigation = "✅ Port tertutup total dari internet publik."
    else:
        portal_status = "🔴 **TERBUKA UNTUK PUBLIK TANPA PASSWORD**"
        posture = "⚠️ **Postur Keamanan:** `PERHATIAN / VULNERABLE TO SCRAPING`"
        mitigation = (
            "🚨 **Rekomendasi Tindakan Keamanan:**\n"
            "Kunci portal arsitektur Anda dengan password menggunakan perintah:\n"
            "`python deploy/set-portal-auth.py enable <username> <password>`\n"
            "*(Bisa juga langsung dari Discord: `!exec python deploy/set-portal-auth.py enable cto Rahasia123!`)*"
        )

    lines = [
        "🛡️ **LAPORAN AUDIT KEAMANAN HOST & PORTAL (PakDir)**",
        posture,
        f"- **Status Portal Port 8080:** {portal_status}",
        f"- **Binding Interface:** `{caddy_stat['port']}`",
        f"- **Firewall Host:** `{fw}`",
        f"\n{mitigation}\n",
        "📋 **Port Listening Terdeteksi di Host:**",
        f"```\n{ports[:600]}\n```",
    ]
    return "\n".join(lines)


def get_top_processes_by_ram(limit: int = 5) -> str:
    """
    Mengambil daftar proses yang memakan memori RAM terbesar di server.
    """
    try:
        import psutil  # type: ignore
        procs = []
        for p in psutil.process_iter(['pid', 'name', 'memory_info', 'memory_percent', 'username']):
            try:
                info = p.info
                rss_mb = (info.get('memory_info').rss // (1024 * 1024)) if info.get('memory_info') else 0
                procs.append({
                    'pid': info.get('pid'),
                    'name': info.get('name') or 'unknown',
                    'user': info.get('username') or '-',
                    'rss_mb': rss_mb,
                    'percent': round(info.get('memory_percent') or 0.0, 1),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        procs.sort(key=lambda x: x['rss_mb'], reverse=True)
        top = procs[:limit]
        if not top:
            return "Tidak ada data proses yang dapat dibaca."
        lines = [f"{'PID':<8} {'USER':<12} {'RAM (MB)':<10} {'RAM %':<8} {'COMMAND/PROCESS'}"]
        lines.append("-" * 60)
        for pr in top:
            lines.append(f"{pr['pid']:<8} {pr['user'][:11]:<12} {pr['rss_mb']:<10} {pr['percent']:<8} {pr['name']}")
        return "\n".join(lines)
    except ImportError:
        pass

    # Fallback Linux 'ps'
    if shutil.which("ps"):
        try:
            res = subprocess.run(
                ["ps", "aux", "--sort=-%mem"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                lines = res.stdout.strip().splitlines()
                return "\n".join(lines[:limit + 1])
        except Exception:
            pass

    # Fallback Windows 'tasklist'
    if shutil.which("tasklist"):
        try:
            res = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                import csv
                reader = csv.reader(res.stdout.strip().splitlines())
                procs = []
                for row in reader:
                    if len(row) >= 5:
                        name, pid = row[0], row[1]
                        mem_str = row[4].replace(".", "").replace(",", "").replace(" K", "").replace("K", "").strip()
                        mem_kb = int(mem_str) if mem_str.isdigit() else 0
                        procs.append((name, pid, mem_kb // 1024))
                procs.sort(key=lambda x: x[2], reverse=True)
                lines = [f"{'PID':<8} {'RAM (MB)':<10} {'IMAGE NAME'}"]
                lines.append("-" * 40)
                for pr in procs[:limit]:
                    lines.append(f"{pr[1]:<8} {pr[2]:<10} {pr[0]}")
                return "\n".join(lines)
        except Exception:
            pass

    return "psutil, ps, atau tasklist tidak tersedia untuk membaca proses."


def get_service_journal_logs(service_name: str, lines: int = 40) -> str:
    """
    Mengambil potongan log journalctl untuk service systemd tertentu secara non-blocking.
    """
    clean_svc = re.sub(r"[^a-zA-Z0-9_\-\.]", "", service_name.strip())
    if not clean_svc:
        return "Nama service tidak valid."

    if shutil.which("journalctl"):
        try:
            res = subprocess.run(
                ["journalctl", "-u", clean_svc, "-n", str(lines), "--no-pager"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            out = (res.stdout or "").strip()
            err = (res.stderr or "").strip()
            combined = out or err
            if combined:
                return combined
            return f"Tidak ada catatan log ditemukan untuk service '{clean_svc}'."
        except subprocess.TimeoutExpired:
            return f"⚠️ Pembacaan log service '{clean_svc}' timeout."
        except Exception as e:
            return f"⚠️ Gagal membaca log service '{clean_svc}': {e}"

    # Fallback Windows event query atau pesan informatif
    return f"journalctl tidak tersedia di lingkungan {platform.system()} untuk membaca log '{clean_svc}'."


def restart_system_service(service_name: str) -> tuple[int, str]:
    """
    Melakukan restart aman layanan systemd dan memverifikasi statusnya.
    """
    clean_svc = re.sub(r"[^a-zA-Z0-9_\-\.]", "", service_name.strip())
    if not clean_svc:
        return 1, "Nama service tidak valid."

    if shutil.which("systemctl"):
        try:
            cmd = ["sudo", "-n", "systemctl", "restart", clean_svc] if os.geteuid() != 0 else ["systemctl", "restart", clean_svc]
        except AttributeError:
            cmd = ["systemctl", "restart", clean_svc]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            if proc.returncode != 0:
                err = proc.stderr.strip() or proc.stdout.strip()
                return proc.returncode, f"Gagal me-restart service '{clean_svc}': {err}"

            # Verifikasi status setelah restart
            check = subprocess.run(["systemctl", "is-active", clean_svc], capture_output=True, text=True, timeout=4)
            status = check.stdout.strip() or "unknown"
            if status == "active":
                return 0, f"✅ Service '{clean_svc}' berhasil di-restart dan berstatus ACTIVE."
            return 1, f"⚠️ Service '{clean_svc}' berhasil dipicu, tetapi status saat ini: '{status}'."
        except Exception as e:
            return 1, f"Error saat me-restart service '{clean_svc}': {e}"

    return 1, f"systemctl tidak tersedia di lingkungan {platform.system()}."


def clean_system_caches(state_db_path: Optional[Path] = None) -> str:
    """
    Membersihkan log dan temporary cache yang tidak terpakai secara aman untuk membebaskan ruang disk & RAM.
    """
    results = []

    # 1. Vacuum journald jika ada
    if shutil.which("journalctl"):
        try:
            res = subprocess.run(["sudo", "-n", "journalctl", "--vacuum-time=7d"], capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                results.append("✅ Journalctl vacuum (7 hari) selesai.")
        except Exception:
            pass

    # 2. Vacuum SQLite jika state_db_path diberikan
    if state_db_path and state_db_path.exists():
        try:
            import sqlite3
            with sqlite3.connect(str(state_db_path)) as conn:
                conn.execute("VACUUM;")
            results.append("✅ SQLite database compacted (VACUUM).")
        except Exception as e:
            results.append(f"⚠️ Gagal compact SQLite: {e}")

    # 3. Clean tmp uploads jika ada
    uploads_dir = Path("data/uploads")
    if uploads_dir.exists():
        cleaned_files = 0
        now = time.time()
        for f in uploads_dir.glob("*"):
            if f.is_file() and (now - f.stat().st_mtime) > (7 * 86400):  # Lebih dari 7 hari
                try:
                    f.unlink()
                    cleaned_files += 1
                except Exception:
                    pass
        if cleaned_files > 0:
            results.append(f"✅ Membersihkan {cleaned_files} berkas upload usang di data/uploads/.")

    if not results:
        results.append("✅ Pemeriksaan cache selesai. Tidak ada berkas sementara yang perlu dibersihkan.")

    return "\n".join(results)

