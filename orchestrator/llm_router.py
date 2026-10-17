import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from orchestrator.state_db import StateDB


# Universal high-speed free models supported across all free tiers
FREE_MODEL_POOLS: Dict[str, List[str]] = {
    "reasoning": [
        "google/gemini-2.5-flash",
        "google/gemini-2.0-flash",
        "google/gemini-1.5-flash",
        "google/gemini-2.5-pro",
        "google/gemini-1.5-pro",
        "groq/llama-3.3-70b-versatile",
        "openrouter/deepseek/deepseek-r1:free",
        "cerebras/llama-3.3-70b",
    ],
    "coding": [
        "google/gemini-2.5-flash",
        "google/gemini-2.0-flash",
        "google/gemini-1.5-flash",
        "groq/llama-3.3-70b-versatile",
        "mistral/codestral-latest",
        "openrouter/qwen/qwen-2.5-coder-32b-instruct:free",
        "cerebras/llama-3.3-70b",
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


def _clean_key(val: Optional[str]) -> str:
    if not val:
        return ""
    return val.strip().strip('"').strip("'")


def _load_agent_system_prompt(agent_name: str) -> str:
    path = Path(".opencode/agent") / f"{agent_name}.md"
    base_prompt = ""
    if path.exists():
        try:
            base_prompt = path.read_text(encoding="utf-8")
        except Exception:
            pass
    if not base_prompt:
        base_prompt = (
            f"You are @{agent_name}, an expert AI engineering professional working for the CTO.\n"
            "Always give structured, professional, production-grade responses."
        )

    try:
        from orchestrator.system_context import get_live_server_context
        live_context = get_live_server_context()
    except Exception:
        live_context = ""

    anti_laziness_directive = (
        "\n\n--- PROTOKOL MUTLAK AI ENGINEER (ANTI-LAZINESS PROTOCOL) ---\n"
        "1. Anda adalah AI Engineer yang bertugas langsung di dalam server VPS host CTO.\n"
        "2. PANTANGAN MUTLAK: DILARANG KERAS menyuruh CTO untuk menjalankan perintah shell/terminal sendiri "
        "(contoh terlarang: 'Silakan Anda jalankan netstat -tuln', 'Buka terminal Anda dan ketik...', 'Anda bisa mengecek dengan...').\n"
        "3. Gunakan data telemetri, port, status layanan, dan info git yang tertera di bawah ini untuk langsung "
        "memberikan jawaban tuntas, faktual, dan konkrit.\n"
        "4. Bila CTO bertanya seputar port, resource, atau status sistem, laporkan hasil analisa Anda secara langsung.\n\n"
        f"{live_context}\n"
    )
    return base_prompt + anti_laziness_directive


def _call_gemini_endpoint(model_name: str, system_prompt: str, prompt: str, api_key: str) -> Tuple[int, str]:
    # 1. Coba endpoint resmi OpenAI-compatible dari Google
    url_openai = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    payload_openai = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 8192,
    }
    try:
        req = urllib.request.Request(
            url_openai,
            data=json.dumps(payload_openai).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices", [])
            if choices and choices[0].get("message", {}).get("content"):
                return 0, choices[0]["message"]["content"]
    except urllib.error.HTTPError as e:
        err_openai = e.read().decode("utf-8", errors="ignore")
        if e.code == 429:
            return 429, f"Gemini 429 Rate Limit: {err_openai}"
        # Jika bukan 429, coba metode generateContent di bawah
    except Exception:
        pass

    # 2. Coba endpoint native generateContent
    url_native = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    payload_native = {
        "contents": [
            {"role": "user", "parts": [{"text": f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\nUSER PROMPT:\n{prompt}"}]}
        ],
        "generationConfig": {
            "maxOutputTokens": 8192,
        },
    }
    try:
        req = urllib.request.Request(
            url_native,
            data=json.dumps(payload_native).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts and parts[0].get("text"):
                    return 0, parts[0]["text"]
    except urllib.error.HTTPError as e:
        err_native = e.read().decode("utf-8", errors="ignore")
        return e.code, f"Gemini HTTP {e.code} ({model_name}): {err_native}"
    except Exception as e:
        return 1, f"Gemini Error ({model_name}): {e}"

    return 1, f"Model {model_name} tidak mengembalikan respons."


def _execute_native_llm(agent_name: str, model: str, prompt: str) -> Tuple[int, str]:
    """
    Eksekutor API langsung (Direct Native LLM Caller) untuk Gemini, Groq, Cerebras,
    OpenRouter, dan Mistral tanpa ketergantungan binary CLI eksternal.
    """
    system_prompt = _load_agent_system_prompt(agent_name)

    # 1. Google Gemini (Google AI Studio)
    if "google" in model or "gemini" in model:
        api_key = _clean_key(os.getenv("GEMINI_API_KEY"))
        if not api_key:
            return 1, "⚠️ GEMINI_API_KEY belum diisi di file .env."

        env_model = _clean_key(os.getenv("GEMINI_MODEL"))
        raw_model = env_model or model.split("/")[-1]

        models_to_try = [raw_model]
        for fallback in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-2.5-pro", "gemini-1.5-pro"):
            if fallback not in models_to_try:
                models_to_try.append(fallback)

        last_gemini_err = ""
        for m_name in models_to_try:
            code, out = _call_gemini_endpoint(m_name, system_prompt, prompt, api_key)
            if code == 0:
                return 0, out
            last_gemini_err = out
            print(f"[LLM Router] Gemini {m_name} gagal: {out[:120]}", flush=True)
            if code == 429:
                return 429, out

        return 1, last_gemini_err

    # 2. Groq Cloud
    if "groq" in model:
        api_key = _clean_key(os.getenv("GROQ_API_KEY"))
        if not api_key:
            return 1, "⚠️ GROQ_API_KEY belum diisi di file .env."
        model_name = model.split("/")[-1]
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": model_name or "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 4096,
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return 0, data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            print(f"[LLM Router] Groq HTTP {e.code}: {err_body}")
            return e.code, f"Groq HTTP {e.code}: {err_body}"
        except Exception as e:
            print(f"[LLM Router] Groq Error: {e}")
            return 1, f"Error koneksi Groq: {e}"

    # 3. Cerebras
    if "cerebras" in model:
        api_key = _clean_key(os.getenv("CEREBRAS_API_KEY"))
        if not api_key:
            return 1, "⚠️ CEREBRAS_API_KEY belum diisi di file .env."
        model_name = model.split("/")[-1]
        url = "https://api.cerebras.ai/v1/chat/completions"
        payload = {
            "model": model_name or "llama3.3-70b",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 4096,
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return 0, data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            print(f"[LLM Router] Cerebras HTTP {e.code}: {err_body}")
            return e.code, f"Cerebras HTTP {e.code}: {err_body}"
        except Exception as e:
            return 1, f"Error koneksi Cerebras: {e}"

    # 4. OpenRouter
    if "openrouter" in model:
        api_key = _clean_key(os.getenv("OPENROUTER_API_KEY"))
        if not api_key:
            return 1, "⚠️ OPENROUTER_API_KEY belum diisi di file .env."
        clean_model = model.replace("openrouter/", "")
        url = "https://openrouter.ai/api/v1/chat/completions"
        payload = {
            "model": clean_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 4096,
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return 0, data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            print(f"[LLM Router] OpenRouter HTTP {e.code}: {err_body}")
            return e.code, f"OpenRouter HTTP {e.code}: {err_body}"
        except Exception as e:
            return 1, f"Error koneksi OpenRouter: {e}"

    # 5. Mistral
    if "mistral" in model:
        api_key = _clean_key(os.getenv("MISTRAL_API_KEY"))
        if not api_key:
            return 1, "⚠️ MISTRAL_API_KEY belum diisi di file .env."
        clean_model = model.replace("mistral/", "")
        url = "https://api.mistral.ai/v1/chat/completions"
        payload = {
            "model": clean_model or "codestral-latest",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return 0, data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            print(f"[LLM Router] Mistral HTTP {e.code}: {err_body}")
            return e.code, f"Mistral HTTP {e.code}: {err_body}"
        except Exception as e:
            return 1, f"Error koneksi Mistral: {e}"

    return 1, f"Provider model '{model}' tidak didukung."


class FreeLLMRouter:
    """
    Multi-Provider Free Fallback Router.
    Mengutamakan OpenCode CLI jika tersedia, dengan failover otomatis ke
    Direct Native LLM API (Gemini/Groq/OpenRouter) saat CLI tidak ditemukan.
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
        return available if available else list(pool)

    @staticmethod
    def _is_rate_limit_error(output: str) -> bool:
        lowered = output.lower()
        return any(sig in lowered for sig in RATE_LIMIT_SIGNALS)

    @staticmethod
    def _default_opencode_runner(agent_name: str, model: str, prompt: str) -> Tuple[int, str]:
        # 1. Coba OpenCode binary jika ada di PATH
        opencode_bin = shutil.which("opencode")
        if opencode_bin:
            cmd = [
                opencode_bin,
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
                if proc.returncode == 0 and combined.strip():
                    return 0, combined.strip()
            except Exception:
                pass

        # 2. Panggilan API Langsung (Native Direct LLM Caller)
        code, out = _execute_native_llm(agent_name, model, prompt)
        if code == 0:
            return 0, out

        return code, out

    def run_agent(self, agent_name: str, prompt: str) -> Dict[str, str]:
        candidates = self.get_candidate_models(agent_name)
        last_error = ""

        # Prioritaskan hanya provider yang kunci API-nya tersedia di lingkungan
        configured_models = []
        unconfigured_models = []
        for m in candidates:
            if ("google" in m or "gemini" in m) and _clean_key(os.getenv("GEMINI_API_KEY")):
                configured_models.append(m)
            elif "groq" in m and _clean_key(os.getenv("GROQ_API_KEY")):
                configured_models.append(m)
            elif "cerebras" in m and _clean_key(os.getenv("CEREBRAS_API_KEY")):
                configured_models.append(m)
            elif "openrouter" in m and _clean_key(os.getenv("OPENROUTER_API_KEY")):
                configured_models.append(m)
            elif "mistral" in m and _clean_key(os.getenv("MISTRAL_API_KEY")):
                configured_models.append(m)
            else:
                unconfigured_models.append(m)

        active_pool = configured_models if configured_models else candidates

        for idx, model in enumerate(active_pool):
            print(f"[LLM Router] Memanggil @{agent_name} dengan model: {model}...")
            start_t = time.time()
            code, output = self.runner(agent_name, model, prompt)
            duration = time.time() - start_t
            if code == 0 and not self._is_rate_limit_error(output):
                print(f"[LLM Router] Sukses dari {model} untuk @{agent_name} ({duration:.2f}s)")
                return {
                    "agent": agent_name,
                    "model": model,
                    "output": output,
                }

            print(f"[LLM Router] Model {model} terkendala ({output[:100]}). Merotasi ke provider berikutnya...")
            self._cooldown_until[model] = time.time() + self.cooldown_seconds
            last_error = output or f"Exit code {code}"
            next_model = active_pool[idx + 1] if idx + 1 < len(active_pool) else "NONE"
            self.state_db.log_llm_rotation(
                agent_name=agent_name,
                failed_model=model,
                selected_model=next_model,
                reason=last_error[:200],
            )

        raise RuntimeError(
            f"Gagal memanggil model untuk @{agent_name}. Catatan error: {last_error}"
        )
