import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


FORBIDDEN_TARGETS = {
    ".env",
    ".git",
    "data/company_state.db",
    "orchestrator",
    "opencode.json",
    "systemd",
    "deploy",
    ".venv",
}

VALID_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css",
    ".json", ".yaml", ".yml", ".toml", ".sql", ".sh", ".bash",
    ".go", ".rs", ".md", ".txt", ".ini", ".cfg",
}


def sanitize_rel_path(raw_path: str) -> Optional[str]:
    """
    Membersihkan dan memvalidasi relative path file.
    Mencegah path traversal (..) dan memastikan ekstensi file valid.
    """
    cleaned = raw_path.strip().strip("`'\" \t\r\n")
    # Ganti backslash dengan forward slash
    cleaned = cleaned.replace("\\", "/")
    # Hapus prefix slash atau ./
    while cleaned.startswith("./") or cleaned.startswith("/"):
        cleaned = cleaned[1:].lstrip("/")

    if not cleaned or ".." in cleaned:
        return None

    path_obj = Path(cleaned)
    if path_obj.suffix.lower() not in VALID_EXTENSIONS:
        return None

    norm = os.path.normpath(cleaned).replace("\\", "/")
    if norm.startswith(".."):
        return None

    return norm


def extract_code_blocks(text: str) -> List[Dict[str, str]]:
    """
    Mengekstrak blok kode dan path file dari teks Markdown yang dihasilkan oleh @dev.
    Mendukung berbagai pola format:
    1. Header baris sebelum blok: '### File: path/to/file.py' atau '**File:** `path/to/file.py`'
    2. Info fence inline: '```python:path/to/file.py' atau '```python file="path/to/file.py"'
    3. Komentar baris pertama dalam kode: '# path/to/file.py' atau '// path/to/file.py'
    """
    results: List[Dict[str, str]] = []
    seen_paths = set()

    # Regex untuk menemukan setiap code block: ```lang\ncode\n```
    # beserta baris-baris sebelum blok kode
    lines = text.splitlines()
    i = 0
    total_lines = len(lines)

    while i < total_lines:
        line = lines[i]
        # Cari baris pembuka kode block
        if line.strip().startswith("```"):
            fence_line = line.strip()
            fence_info = fence_line.lstrip("`").strip()

            # Kumpulkan isi kode block
            code_lines = []
            i += 1
            while i < total_lines and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1

            code_content = "\n".join(code_lines).rstrip()

            # 1. Coba ekstrak dari fence_info (misal: ```python:src/app.py atau ```python file="src/app.py")
            detected_path = None
            if ":" in fence_info:
                parts = fence_info.split(":", 1)
                candidate = parts[1].strip()
                detected_path = sanitize_rel_path(candidate)
            elif "file=" in fence_info:
                candidate = fence_info.split("file=", 1)[1].split()[0]
                detected_path = sanitize_rel_path(candidate)
            elif "path=" in fence_info:
                candidate = fence_info.split("path=", 1)[1].split()[0]
                detected_path = sanitize_rel_path(candidate)

            # 2. Jika belum ketemu, cek 1-3 baris sebelum pembuka code block
            if not detected_path:
                start_back = max(0, i - len(code_lines) - 4)
                end_back = max(0, i - len(code_lines) - 1)
                preceding_lines = lines[start_back:end_back]
                for p_line in reversed(preceding_lines):
                    p_clean = p_line.strip()
                    if not p_clean:
                        continue
                    # Pola seperti: ### File: src/main.py atau **File:** `src/main.py` atau FILE: src/main.py
                    m = re.search(
                        r"(?:#{1,4}\s*|\[|\*\*|FILE:\s*|File:\s*|Berkas:\s*)"
                        r"(?:[Ff]ile|[Bb]erkas|[Pp]ath)?"
                        r"[:\s*]*[`'\"]?([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9_]+)[`'\"]?",
                        p_clean,
                    )
                    if m:
                        candidate = m.group(1)
                        valid_path = sanitize_rel_path(candidate)
                        if valid_path:
                            detected_path = valid_path
                            break

            # 3. Jika belum ketemu, cek baris pertama dari kode jika berupa komentar nama file
            if not detected_path and code_lines:
                first_line = code_lines[0].strip()
                m_comment = re.search(
                    r"^(?:#|//|/\*|--)\s*(?:[Ff]ile:\s*)?([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9_]+)",
                    first_line,
                )
                if m_comment:
                    valid_path = sanitize_rel_path(m_comment.group(1))
                    if valid_path:
                        detected_path = valid_path

            if detected_path and detected_path not in seen_paths:
                seen_paths.add(detected_path)
                results.append({
                    "path": detected_path,
                    "content": code_content,
                })

        i += 1

    return results


def write_extracted_code_files(
    blocks: List[Dict[str, str]],
    root_dir: str | Path,
) -> List[Path]:
    """
    Menulis seluruh blok kode yang diekstrak ke sistem berkas nyata di dalam root_dir.
    Menerapkan pengamanan direktori agar tidak menimpa berkas kritis sistem.
    """
    root_path = Path(root_dir).resolve()
    written_files: List[Path] = []

    for item in blocks:
        rel_str = item["path"]
        content = item["content"]

        # Cek apakah target dilarang
        if any(rel_str == forb or rel_str.startswith(f"{forb}/") for forb in FORBIDDEN_TARGETS):
            continue

        target_file = (root_path / rel_str).resolve()

        # Validasi path traversal
        try:
            target_file.relative_to(root_path)
        except ValueError:
            continue

        # Buat direktori induk jika belum ada
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(content, encoding="utf-8")
        written_files.append(target_file)

    return written_files
