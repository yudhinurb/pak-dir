import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional


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
        branch = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, timeout=3).stdout.strip()
        last_log = subprocess.run(["git", "log", "-1", "--oneline"], capture_output=True, text=True, timeout=3).stdout.strip()
        diff_stat = subprocess.run(["git", "status", "-s"], capture_output=True, text=True, timeout=3).stdout.strip()
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
        branch = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, timeout=3).stdout.strip()
        last_log = subprocess.run(["git", "log", "-1", "--oneline"], capture_output=True, text=True, timeout=3).stdout.strip()
        return f"{branch or 'main'} ({last_log})"
    except Exception:
        return "main"


def execute_server_command(cmd: str, timeout: int = 15) -> str:
    """
    Mengeksekusi perintah terminal atas perintah langsung CTO.
    """
    try:
        # Jalankan di bash jika Linux, atau shell default
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
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
