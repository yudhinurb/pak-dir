import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ekstensi teks & kode yang didukung untuk ekstraksi langsung
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".sql",
    ".csv",
    ".log",
    ".sh",
    ".bash",
    ".env.example",
    ".toml",
    ".xml",
    ".c",
    ".cpp",
    ".h",
    ".go",
    ".rs",
    ".php",
    ".java",
}

MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024  # 15 MB
MAX_EXTRACT_CHARS = 60000  # ~12,000 tokens untuk menjaga efisiensi memory & context window


def sanitize_filename(filename: str) -> str:
    """Membersihkan nama file dari karakter berbahaya."""
    base = Path(filename).name
    clean = re.sub(r"[^a-zA-Z0-9_.-]", "_", base)
    return clean or "uploaded_file"


def is_supported_file(filename: str) -> bool:
    """Mengecek apakah file didukung untuk diekstrak isinya."""
    ext = Path(filename).suffix.lower()
    return ext == ".pdf" or ext in TEXT_EXTENSIONS


def extract_text_from_pdf(raw_bytes: bytes, max_chars: int = MAX_EXTRACT_CHARS) -> Tuple[bool, str, int]:
    """
    Mengekstrak teks digital dari PDF secara efisien menggunakan pypdf.
    Jejak memori < 15 MB dan tanpa dependensi C eksternal.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return (
            False,
            "⚠️ Pustaka 'pypdf' belum terpasang di host. Silakan jalankan: pip install pypdf",
            0,
        )

    try:
        reader = PdfReader(io.BytesIO(raw_bytes))
        num_pages = len(reader.pages)
        if num_pages == 0:
            return False, "Dokumen PDF kosong atau tidak memiliki halaman.", 0

        pages_text: List[str] = []
        total_len = 0

        for idx, page in enumerate(reader.pages):
            txt = page.extract_text() or ""
            txt_clean = txt.strip()
            if txt_clean:
                pages_text.append(f"--- [Halaman {idx + 1}] ---\n{txt_clean}")
                total_len += len(txt_clean)

            if total_len >= max_chars:
                pages_text.append("\n[Peringatan: Teks dokumen mencapai batas maksimum kuota token, dipotong otomatis]")
                break

        full_text = "\n\n".join(pages_text)
        if not full_text.strip():
            return (
                True,
                "⚠️ Dokumen PDF ini berupa scan gambar murni tanpa lapisan teks digital (image-only/scanned PDF).",
                num_pages,
            )

        return True, full_text, num_pages
    except Exception as exc:
        return False, f"Gagal mengekstrak dokumen PDF: {exc}", 0


def extract_text_from_code_or_text(raw_bytes: bytes, max_chars: int = MAX_EXTRACT_CHARS) -> Tuple[bool, str]:
    """
    Mendekode file teks / kode sumber menggunakan UTF-8 dengan fallback Latin-1.
    """
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw_bytes.decode("latin-1")
        except Exception as exc:
            return False, f"Gagal membaca format teks: {exc}"

    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[Peringatan: Teks dipotong pada batas maksimum kuota karakter]"

    return True, text


def save_uploaded_file(filename: str, raw_bytes: bytes, upload_dir: Path) -> Path:
    """
    Menyimpan salinan file ke folder data/uploads dengan timestamp unik.
    """
    upload_dir.mkdir(parents=True, exist_ok=True)
    clean_name = sanitize_filename(filename)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_path = upload_dir / f"{timestamp}_{clean_name}"
    target_path.write_bytes(raw_bytes)
    return target_path


def process_single_attachment(
    filename: str,
    raw_bytes: bytes,
    upload_dir: Path,
) -> Dict[str, Any]:
    """
    Memproses satu attachment Discord: validasi, simpan ke disk, dan ekstrak teks.
    """
    size_bytes = len(raw_bytes)
    size_kb = size_bytes / 1024
    ext = Path(filename).suffix.lower()

    if size_bytes > MAX_FILE_SIZE_BYTES:
        return {
            "filename": filename,
            "success": False,
            "error": f"Ukuran file ({size_kb:.1f} KB) melebihi batas aman maksimum (15 MB).",
            "size_kb": size_kb,
            "saved_path": None,
            "content": "",
            "is_pdf": ext == ".pdf",
            "pages": 0,
        }

    # Simpan file ke arsip disk
    saved_path = save_uploaded_file(filename, raw_bytes, upload_dir)

    # Ekstraksi konten
    if ext == ".pdf":
        ok, content, pages = extract_text_from_pdf(raw_bytes)
        return {
            "filename": filename,
            "success": ok,
            "error": "" if ok else content,
            "size_kb": size_kb,
            "saved_path": str(saved_path),
            "content": content if ok else "",
            "is_pdf": True,
            "pages": pages,
        }
    elif ext in TEXT_EXTENSIONS or ext == "":
        ok, content = extract_text_from_code_or_text(raw_bytes)
        return {
            "filename": filename,
            "success": ok,
            "error": "" if ok else content,
            "size_kb": size_kb,
            "saved_path": str(saved_path),
            "content": content if ok else "",
            "is_pdf": False,
            "pages": 1,
        }
    else:
        return {
            "filename": filename,
            "success": False,
            "error": f"Format file `{ext}` belum didukung untuk ekstraksi teks otomatis.",
            "size_kb": size_kb,
            "saved_path": str(saved_path),
            "content": "",
            "is_pdf": False,
            "pages": 0,
        }


def format_attachments_context(processed_list: List[Dict[str, Any]]) -> str:
    """
    Menyusun blok teks dokumen terlampir untuk disematkan ke dalam prompt LLM.
    """
    if not processed_list:
        return ""

    blocks = ["\n=== [DOKUMEN & BERKAS TERLAMPIR DARI CTO] ==="]
    for item in processed_list:
        fname = item["filename"]
        size_kb = item.get("size_kb", 0)
        if item.get("success"):
            pages_info = f" ({item['pages']} halaman)" if item.get("is_pdf") else ""
            blocks.append(
                f"\n📁 **Nama Berkas:** `{fname}` ({size_kb:.1f} KB){pages_info}\n"
                f"```text\n{item['content']}\n```"
            )
        else:
            blocks.append(f"\n⚠️ **Nama Berkas:** `{fname}` (Gagal diekstrak: {item.get('error')})")

    blocks.append("=== [AKHIR DOKUMEN TERLAMPIR] ===\n")
    return "\n".join(blocks)
