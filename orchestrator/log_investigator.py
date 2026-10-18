import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from orchestrator.llm_router import FreeLLMRouter
from orchestrator.state_db import StateDB
from orchestrator.system_context import (
    get_live_server_context,
    get_service_journal_logs,
)


INVESTIGATOR_SYSTEM_PROMPT = """You are @investigator, an elite AI Site Reliability Engineer (SRE) and Systems Investigator.
Your mission is to perform fast, accurate Root Cause Analysis (RCA) on server crashes, stack traces, and system error logs.

--- PROTOKOL MUTLAK INVESTIGATOR (ANTI-LAZINESS PROTOCOL) ---
1. Berikan analisis faktual, tajam, dan langsung ke akar masalah.
2. DILARANG KERAS berspekulasi tanpa dasar log atau menyuruh CTO mengetik perintah investigasi sendiri di terminal jika datanya sudah tersedia.
3. Selalu format laporan Anda dengan struktur bersih berikut:
   ### 🔴 Akar Masalah (Root Cause)
   [Penjelasan konkrit mengapa error ini terjadi]

   ### 📋 Bukti Log Kunci
   ```
   [Tampilkan 2-5 baris log paling krusial yang menjadi biang kerok]
   ```

   ### 🛠️ Solusi & Rekomendasi Pemulihan
   - **Tindakan Cepat (Immediate):** [Contoh: `!restart caddy`, `!clean`, atau kill PID x]
   - **Solusi Permanen (Permanent):** [Contoh: Perbaikan konfigurasi timeout, penambahan indeks DB, atau patch memori]
"""


class LogInvestigator:
    """
    Mesin Triage & Investigasi Log Cerdas.
    Menganalisis log systemd, Docker, atau berkas teks/log yang diunggah CTO dari Discord
    menggunakan Free LLM Router (Gemini Flash-Lite / Groq / Cerebras).
    """

    def __init__(
        self,
        router: Optional[FreeLLMRouter] = None,
        state_db: Optional[StateDB] = None,
    ) -> None:
        self.state_db = state_db or StateDB()
        self.router = router or FreeLLMRouter(state_db=self.state_db)

    def investigate_service(self, service_name: str, lines: int = 50) -> Dict[str, Any]:
        """
        Mengambil log systemd journalctl untuk service tertentu dan melakukan RCA otomatis.
        """
        clean_name = re.sub(r"[^a-zA-Z0-9_\-\.]", "", service_name.strip())
        raw_logs = get_service_journal_logs(clean_name, lines=lines)
        live_ctx = get_live_server_context()

        prompt = (
            f"Lakukan Root Cause Analysis (RCA) untuk layanan: `{clean_name}`\n\n"
            f"LOG JOURNAL TERAKHIR ({lines} baris):\n"
            f"```\n{raw_logs[-4000:]}\n```\n\n"
            f"KONDISI HOST SAAT INI:\n{live_ctx}\n\n"
            "Tentukan akar masalah kegagalan/anomali dan berikan solusi pemulihan konkrit."
        )

        res = self.router.run_agent("investigator", prompt)
        return {
            "service": clean_name,
            "raw_logs": raw_logs,
            "analysis": res["output"],
            "model": res["model"],
        }

    def investigate_log_text(self, log_content: str, source_label: str = "Uploaded Log") -> Dict[str, Any]:
        """
        Menganalisis teks log atau stack trace bebas (misal hasil upload file dari Discord).
        """
        if not log_content.strip():
            return {
                "source": source_label,
                "analysis": "Log yang diberikan kosong. Tidak ada error yang dapat dianalisis.",
                "model": "none",
            }

        live_ctx = get_live_server_context()
        prompt = (
            f"Sumber Log: `{source_label}`\n\n"
            f"KONTEN LOG/STACK TRACE:\n"
            f"```\n{log_content[-6000:]}\n```\n\n"
            f"KONDISI HOST SAAT INI:\n{live_ctx}\n\n"
            "Analisis log di atas, temukan akar masalah, dan berikan rekomendasi pemulihan."
        )

        res = self.router.run_agent("investigator", prompt)
        return {
            "source": source_label,
            "analysis": res["output"],
            "model": res["model"],
        }
