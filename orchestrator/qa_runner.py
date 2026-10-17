import os
import py_compile
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple


def check_python_syntax(files: List[Path]) -> Tuple[int, str]:
    """
    Memeriksa sintaksis Python pada seluruh berkas .py yang dibuat/diubah.
    Mengembalikan (0, 'OK') jika sintaks valid, atau (1, error_message) jika ada SyntaxError.
    """
    for f in files:
        if f.suffix.lower() == ".py" and f.exists():
            try:
                py_compile.compile(str(f), doraise=True)
            except py_compile.PyCompileError as exc:
                return 1, f"SyntaxError pada berkas {f.name}:\n{exc}"
            except Exception as exc:
                return 1, f"Gagal mengompilasi berkas {f.name}: {exc}"
    return 0, "Syntax check passed"


def run_real_qa(
    root_dir: str | Path,
    written_files: Optional[List[Path]] = None,
    timeout: int = 40,
) -> Tuple[int, str]:
    """
    Eksekutor QA otomatis nyata di server:
    1. Memeriksa validitas sintaksis seluruh file Python yang baru dibuat/diedit.
    2. Mendeteksi dan mengeksekusi test runner nyata (unittest) di server.
    3. Mengembalikan returncode aktual (0 jika lulus, != 0 jika gagal) beserta trace log lengkap.
    """
    root_path = Path(root_dir).resolve()
    target_files = written_files or []

    # 1. Syntax Check
    py_files = [f for f in target_files if f.suffix.lower() == ".py"]
    if py_files:
        syn_code, syn_msg = check_python_syntax(py_files)
        if syn_code != 0:
            return syn_code, f"❌ QA SYNTAX CHECK FAILED:\n{syn_msg}"

    # 2. Cari berkas test yang relevan
    # Utamakan test baru yang dibuat oleh @dev
    new_tests = [
        f for f in target_files
        if f.suffix.lower() == ".py" and (f.name.startswith("test_") or f.name.endswith("_test.py"))
    ]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(root_path) + os.pathsep + env.get("PYTHONPATH", "")

    # Jalankan test baru jika ada
    if new_tests:
        test_paths = [str(t.relative_to(root_path)) if t.is_relative_to(root_path) else str(t) for t in new_tests]
        cmd = [sys.executable, "-m", "unittest"] + test_paths
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(root_path),
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout,
            )
            out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            if proc.returncode != 0:
                return proc.returncode, f"❌ REAL AUTOMATED TEST FAILED (Exit Code {proc.returncode}):\n{out.strip()}"
            return 0, f"✅ REAL AUTOMATED TEST PASSED:\n{out.strip()}"
        except subprocess.TimeoutExpired:
            return 1, f"❌ REAL AUTOMATED TEST TIMEOUT setelah {timeout}s"
        except Exception as exc:
            return 1, f"❌ Error saat menjalankan test: {exc}"

    # Jika tidak ada test baru khusus, cek apakah ada direktori tests/ di root proyek
    tests_dir = root_path / "tests"
    has_test_files = tests_dir.exists() and any(tests_dir.glob("test_*.py"))

    # Jika tests_dir ada dan bukan hanya test_orchestrator, jalankan test suite ringan
    if has_test_files:
        cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(root_path),
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout,
            )
            out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            if proc.returncode != 0:
                return proc.returncode, f"❌ TEST SUITE FAILED (Exit Code {proc.returncode}):\n{out.strip()}"
            return 0, f"✅ TEST SUITE PASSED:\n{out.strip()}"
        except subprocess.TimeoutExpired:
            return 1, f"❌ TEST SUITE TIMEOUT setelah {timeout}s"
        except Exception as exc:
            return 1, f"❌ Error saat menjalankan test suite: {exc}"

    return 0, "✅ Syntax check passed. (Tidak ada automated test file tambahan yang perlu dijalankan)."
