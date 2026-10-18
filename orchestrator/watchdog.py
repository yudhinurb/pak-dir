import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from orchestrator.state_db import StateDB
from orchestrator.system_context import (
    get_listening_ports,
    get_top_processes_by_ram,
)


class SentinelWatchdog:
    """
    Mesin Pemantau Latar Belakang Otonom (Watchdog & Health Sentinel).
    Memeriksa kesehatan host VPS secara berkala:
    1. Ambang batas RAM, Swap, dan Disk.
    2. Status layanan inti (systemd / host services).
    3. Anomali port terbuka.
    4. Mencegah notifikasi berulang menggunakan cooldown per alert_type.
    """

    DEFAULT_SERVICES = ["caddy", "pakdir-orchestrator", "ssh"]

    def __init__(
        self,
        state_db: Optional[StateDB] = None,
        max_ram_percent: float = 85.0,
        max_swap_percent: float = 60.0,
        max_disk_percent: float = 88.0,
        monitored_services: Optional[List[str]] = None,
        alert_cooldown_seconds: float = 1800.0,  # 30 menit
    ) -> None:
        self.state_db = state_db or StateDB()
        self.max_ram_percent = max_ram_percent
        self.max_swap_percent = max_swap_percent
        self.max_disk_percent = max_disk_percent

        env_services = os.getenv("MONITORED_SERVICES")
        if env_services:
            self.monitored_services = [s.strip() for s in env_services.split(",") if s.strip()]
        else:
            self.monitored_services = monitored_services or list(self.DEFAULT_SERVICES)

        self.alert_cooldown_seconds = alert_cooldown_seconds
        self._last_alert_times: Dict[str, float] = {}

    def is_in_cooldown(self, alert_key: str) -> bool:
        """Mengecek apakah alert_key tertentu masih berada dalam masa cooldown."""
        now = time.time()
        last_t = self._last_alert_times.get(alert_key, 0.0)
        if (now - last_t) < self.alert_cooldown_seconds:
            return True
        return False

    def mark_alert_sent(self, alert_key: str) -> None:
        """Mencatat waktu pengiriman alert untuk mengaktifkan cooldown."""
        self._last_alert_times[alert_key] = time.time()

    def get_memory_metrics(self) -> Dict[str, Any]:
        """Mengambil data pemakaian RAM dan Swap."""
        try:
            import psutil  # type: ignore
            vm = psutil.virtual_memory()
            sm = psutil.swap_memory()
            return {
                "ram_percent": round(vm.percent, 1),
                "ram_used_mb": vm.used // (1024 * 1024),
                "ram_total_mb": vm.total // (1024 * 1024),
                "swap_percent": round(sm.percent, 1),
                "swap_used_mb": sm.used // (1024 * 1024),
                "swap_total_mb": sm.total // (1024 * 1024),
            }
        except ImportError:
            pass

        # Fallback Linux /proc/meminfo
        if Path("/proc/meminfo").exists():
            try:
                mem: Dict[str, int] = {}
                for line in Path("/proc/meminfo").read_text().splitlines():
                    parts = line.split(":")
                    if len(parts) == 2:
                        k = parts[0].strip()
                        v = int(parts[1].strip().split()[0]) // 1024  # MB
                        mem[k] = v
                total = mem.get("MemTotal", 1)
                avail = mem.get("MemAvailable", total)
                used = max(0, total - avail)
                percent = round((used / total) * 100, 1)
                swap_total = mem.get("SwapTotal", 0)
                swap_free = mem.get("SwapFree", 0)
                swap_used = max(0, swap_total - swap_free)
                swap_percent = round((swap_used / swap_total) * 100, 1) if swap_total > 0 else 0.0
                return {
                    "ram_percent": percent,
                    "ram_used_mb": used,
                    "ram_total_mb": total,
                    "swap_percent": swap_percent,
                    "swap_used_mb": swap_used,
                    "swap_total_mb": swap_total,
                }
            except Exception:
                pass

        return {
            "ram_percent": 0.0,
            "ram_used_mb": 0,
            "ram_total_mb": 0,
            "swap_percent": 0.0,
            "swap_used_mb": 0,
            "swap_total_mb": 0,
        }

    def check_memory(self) -> Optional[Dict[str, Any]]:
        """Mengecek apakah pemakaian RAM atau Swap melampaui batas bahaya."""
        metrics = self.get_memory_metrics()
        ram_p = metrics["ram_percent"]
        swap_p = metrics["swap_percent"]

        if ram_p >= self.max_ram_percent or (swap_p >= self.max_swap_percent and metrics["swap_total_mb"] > 0):
            severity = "CRITICAL" if ram_p >= 92.0 else "WARNING"
            top_procs = get_top_processes_by_ram(limit=4)
            return {
                "type": "MEMORY_SPIKE",
                "severity": severity,
                "title": f"⚠️ Pemakaian Memori Tinggi ({ram_p}% RAM, {swap_p}% Swap)",
                "details": (
                    f"Pemakaian RAM saat ini mencapai {ram_p}% ({metrics['ram_used_mb']} MB / {metrics['ram_total_mb']} MB).\n"
                    f"Swap: {swap_p}% ({metrics['swap_used_mb']} MB / {metrics['swap_total_mb']} MB).\n\n"
                    f"**Proses Teratas Berdasarkan Konsumsi RAM:**\n```\n{top_procs}\n```\n"
                    "Rekomendasi Tindakan:\n"
                    "- Ketik `!top` untuk melihat proses lengkap.\n"
                    "- Ketik `!clean` untuk membersihkan cache sistem.\n"
                    "- Ketik `!restart <service>` jika ada service yang bocor memori."
                ),
            }
        return None

    def check_disk(self) -> Optional[Dict[str, Any]]:
        """Mengecek ruang partisi disk utama."""
        try:
            total, used, free = shutil.disk_usage(".")
            used_percent = round((used / total) * 100, 1)
            free_gb = round(free / (1024**3), 2)
            total_gb = round(total / (1024**3), 2)
            if used_percent >= self.max_disk_percent:
                severity = "CRITICAL" if used_percent >= 95.0 else "WARNING"
                return {
                    "type": "DISK_FULL",
                    "severity": severity,
                    "title": f"💾 Ruang Disk Menipis ({used_percent}% terpakai)",
                    "details": (
                        f"Partisi disk tersisa hanya {free_gb} GB dari total {total_gb} GB ({used_percent}% terpakai).\n"
                        "Rekomendasi Tindakan:\n"
                        "- Ketik `!clean` untuk membersihkan log journal lama dan cache.\n"
                        "- Hapus Docker images atau temporary files yang tidak terpakai."
                    ),
                }
        except Exception:
            pass
        return None

    def check_services(self) -> List[Dict[str, Any]]:
        """Mengecek apakah ada service kritis yang mati (inactive/failed)."""
        alerts: List[Dict[str, Any]] = []
        if not shutil.which("systemctl"):
            return alerts

        for svc in self.monitored_services:
            try:
                res = subprocess.run(
                    ["systemctl", "is-active", svc],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                status = res.stdout.strip()
                if status in ("failed", "inactive", "dead"):
                    alerts.append({
                        "type": f"SERVICE_DOWN_{svc.upper()}",
                        "severity": "CRITICAL",
                        "title": f"🚨 Layanan '{svc}' Mati (Status: {status.upper()})",
                        "details": (
                            f"Layanan systemd '{svc}' berstatus `{status}` di host.\n\n"
                            f"Rekomendasi Tindakan Cepat:\n"
                            f"- Ketik `!logs {svc}` untuk melihat penyebab crash.\n"
                            f"- Ketik `!restart {svc}` untuk memulihkan layanan segera."
                        ),
                        "service": svc,
                    })
            except Exception:
                pass

        return alerts

    def inspect_system(self) -> List[Dict[str, Any]]:
        """
        Menjalankan seluruh pemantauan dan mengembalikan daftar alert yang belum dalam masa cooldown.
        Mencatat setiap alert baru ke dalam database insiden (StateDB).
        """
        active_alerts: List[Dict[str, Any]] = []

        # 1. Cek Memori
        mem_alert = self.check_memory()
        if mem_alert:
            key = mem_alert["type"]
            if not self.is_in_cooldown(key):
                active_alerts.append(mem_alert)
                self.mark_alert_sent(key)
                self.state_db.create_incident(
                    alert_type=mem_alert["type"],
                    severity=mem_alert["severity"],
                    details=mem_alert["details"],
                )

        # 2. Cek Disk
        disk_alert = self.check_disk()
        if disk_alert:
            key = disk_alert["type"]
            if not self.is_in_cooldown(key):
                active_alerts.append(disk_alert)
                self.mark_alert_sent(key)
                self.state_db.create_incident(
                    alert_type=disk_alert["type"],
                    severity=disk_alert["severity"],
                    details=disk_alert["details"],
                )

        # 3. Cek Services
        for svc_alert in self.check_services():
            key = svc_alert["type"]
            if not self.is_in_cooldown(key):
                active_alerts.append(svc_alert)
                self.mark_alert_sent(key)
                self.state_db.create_incident(
                    alert_type=svc_alert["type"],
                    severity=svc_alert["severity"],
                    details=svc_alert["details"],
                )

        return active_alerts
