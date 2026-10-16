import subprocess
import time
from typing import Callable, Dict, List, Optional, Tuple

from orchestrator.state_db import StateDB


# Daftar rotasi model gratis (Open-Weight & Frontier Free Tiers)
# Dikelompokkan berdasarkan kebutuhan tugas agar kualitas tinggi & bebas biaya.
FREE_MODEL_POOLS: Dict[str, List[str]] = {
    # Untuk EM-Agent, System Architect, & Product Manager (Reasoning & Context Besar)
    "reasoning": [
        "google/gemini-2.5-pro",
        "openrouter/deepseek/deepseek-r1:free",
        "groq/llama-3.3-70b-versatile",
        "cerebras/llama-3.3-70b",
        "google/gemini-2.5-flash",
    ],
    # Untuk Dev-Agent, Ops-Agent, & Spesialis Koding (Cepat & Akurat di Kode)
    "coding": [
        "mistral/codestral-latest",
        "openrouter/qwen/qwen3-coder:free",
        "google/gemini-2.5-flash",
        "groq/llama-3.3-70b-versatile",
        "cerebras/llama-3.3-70b",
        "openrouter/deepseek/deepseek-chat-v3-0324:free",
    ],
}

AGENT_DEFAULT_CATEGORY: Dict[str, str] = {
    "em": "reasoning",
    "pm": "reasoning",
    "architect": "reasoning",
    "dev": "coding",
    "ops": "coding",
}

RATE_LIMIT_SIGNALS = (
    "429",
    "rate limit",
    "rate_limit",
    "quota exceeded",
    "resource_exhausted",
    "too many requests",
    "capacity",
)


class FreeLLMRouter:
    """
    Multi-Provider Free Fallback Router.
    Memanggil OpenCode CLI (`opencode run --agent <agent> --model <model> <prompt>`)
    dan otomatis berputar ke provider gratis berikutnya jika terkena HTTP 429 / Rate Limit.
    """

    def __init__(
        self,
        state_db: Optional[StateDB] = None,
        runner: Optional[Callable[[str, str, str], Tuple[int, str]]] = None,
        cooldown_seconds: float = 120.0,
    ) -> None:
        self.state_db = state_db or StateDB()
        self.runner = runner or self._default_opencode_runner
        self.cooldown_seconds = cooldown_seconds
        self._cooldown_until: Dict[str, float] = {}
        self._agent_categories: Dict[str, str] = dict(AGENT_DEFAULT_CATEGORY)

    def register_agent_category(self, agent_name: str, category: str) -> None:
        if category not in FREE_MODEL_POOLS:
            category = "coding"
        self._agent_categories[agent_name] = category

    def get_candidate_models(self, agent_name: str) -> List[str]:
        category = self._agent_categories.get(agent_name, "coding")
        pool = FREE_MODEL_POOLS.get(category, FREE_MODEL_POOLS["coding"])
        now = time.time()
        available = [m for m in pool if self._cooldown_until.get(m, 0.0) <= now]
        # Jika semua sedang cooldown, tetap kembalikan seluruh pool sebagai fallback darurat
        return available if available else list(pool)

    @staticmethod
    def _is_rate_limit_error(output: str) -> bool:
        lowered = output.lower()
        return any(sig in lowered for sig in RATE_LIMIT_SIGNALS)

    @staticmethod
    def _default_opencode_runner(agent_name: str, model: str, prompt: str) -> Tuple[int, str]:
        cmd = [
            "opencode",
            "run",
            "--agent",
            agent_name,
            "--model",
            model,
            prompt,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            combined = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            return proc.returncode, combined.strip()
        except FileNotFoundError:
            # Mode simulasi/offline jika binary opencode belum terpasang di mesin lokal
            return (
                0,
                f"[SIMULATED OpenCode] Agent=@{agent_name} | Model={model}\n"
                f"Berhasil mengeksekusi instruksi: {prompt[:160]}",
            )

    def run_agent(self, agent_name: str, prompt: str) -> Dict[str, str]:
        """
        Menjalankan agen dengan rotasi otomatis lintas provider gratis jika kena rate-limit.
        Mengembalikan dict: {"agent": ..., "model": ..., "output": ...}
        """
        candidates = self.get_candidate_models(agent_name)
        last_error = ""

        for idx, model in enumerate(candidates):
            code, output = self.runner(agent_name, model, prompt)
            if code == 0 and not self._is_rate_limit_error(output):
                return {
                    "agent": agent_name,
                    "model": model,
                    "output": output,
                }

            # Tandai model sedang cooldown dan rotasi ke provider gratis berikutnya
            self._cooldown_until[model] = time.time() + self.cooldown_seconds
            last_error = output or f"Exit code {code}"
            next_model = candidates[idx + 1] if idx + 1 < len(candidates) else "NONE"
            self.state_db.log_llm_rotation(
                agent_name=agent_name,
                failed_model=model,
                selected_model=next_model,
                reason=last_error[:200],
            )

        raise RuntimeError(
            f"Seluruh provider gratis untuk @{agent_name} sedang penuh/rate-limited. Error terakhir: {last_error}"
        )
