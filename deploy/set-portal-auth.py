import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def get_default_caddyfile() -> Path:
    """Mencari lokasi Caddyfile aktif (prioritas server produksi lalu lokal repo)."""
    prod_path = Path("/etc/caddy/Caddyfile")
    if prod_path.exists():
        return prod_path

    # Fallback ke path repository lokal
    local_path = Path(__file__).resolve().parent / "Caddyfile"
    return local_path


def generate_bcrypt_hash(plaintext: str) -> str:
    """
    Menghasilkan hash Bcrypt standar untuk Caddy basicauth.
    Mendukung caddy hash-password (jika Caddy terpasang) atau pustaka python bcrypt.
    """
    # 1. Coba via caddy CLI jika tersedia di sistem
    caddy_bin = shutil.which("caddy")
    if caddy_bin:
        try:
            res = subprocess.run(
                [caddy_bin, "hash-password", "--plaintext", plaintext],
                capture_output=True,
                text=True,
                timeout=5,
            )
            out = res.stdout.strip()
            if res.returncode == 0 and out.startswith("$2"):
                return out
        except Exception:
            pass

    # 2. Coba via pustaka python bcrypt
    try:
        import bcrypt
        hashed = bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt(rounds=12))
        return hashed.decode("utf-8")
    except ImportError:
        pass

    raise RuntimeError(
        "Gagal membuat hash Bcrypt: tidak ditemukan binary 'caddy' maupun pustaka 'bcrypt'. "
        "Silakan jalankan: pip install bcrypt"
    )


def reload_caddy() -> bool:
    """Me-reload konfigurasi Caddy secara instan tanpa downtime."""
    if shutil.which("systemctl"):
        try:
            res = subprocess.run(["sudo", "systemctl", "reload", "caddy"], capture_output=True, timeout=5)
            return res.returncode == 0
        except Exception:
            pass
    return False


def get_auth_status(caddyfile_path: Optional[Path] = None) -> Dict[str, Any]:
    """Mengecek status autentikasi dan binding port pada Caddyfile."""
    path = caddyfile_path or get_default_caddyfile()
    if not path.exists():
        return {
            "caddyfile": str(path),
            "exists": False,
            "auth_enabled": False,
            "username": None,
            "is_loopback": False,
            "port": "8080",
        }

    content = path.read_text(encoding="utf-8")
    is_loopback = bool(re.search(r"127\.0\.0\.1:8080|localhost:8080", content))
    
    # Cari blok basicauth yang aktif (tidak diawali tanda #)
    match = re.search(r"(?m)^\s*basicauth\s+\*\s*\{\s*([a-zA-Z0-9_-]+)\s+(\$2[abxy]?\$[^\s]+)", content)
    if match:
        return {
            "caddyfile": str(path),
            "exists": True,
            "auth_enabled": True,
            "username": match.group(1),
            "is_loopback": is_loopback,
            "port": "127.0.0.1:8080" if is_loopback else ":8080",
        }

    return {
        "caddyfile": str(path),
        "exists": True,
        "auth_enabled": False,
        "username": None,
        "is_loopback": is_loopback,
        "port": "127.0.0.1:8080" if is_loopback else ":8080",
    }


def _safe_write_caddyfile(path: Path, new_content: str) -> None:
    """Menulis ke Caddyfile dengan fallback otomatis sudo tee jika terjadi PermissionError."""
    try:
        path.write_text(new_content, encoding="utf-8")
    except PermissionError:
        proc = subprocess.run(
            ["sudo", "tee", str(path)],
            input=new_content,
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            raise PermissionError(
                f"Izin ditolak saat menulis ke {path}. Silakan jalankan dengan sudo:\n"
                f"sudo .venv/bin/python deploy/set-portal-auth.py ..."
            )


def enable_basic_auth(username: str, plaintext: str, caddyfile_path: Optional[Path] = None) -> Tuple[bool, str]:
    """Mengaktifkan proteksi password HTTP Basic Auth pada Caddyfile."""
    path = caddyfile_path or get_default_caddyfile()
    if not path.exists():
        return False, f"File {path} tidak ditemukan."

    clean_user = re.sub(r"[^a-zA-Z0-9_-]", "", username.strip())
    if not clean_user:
        return False, "Username tidak valid."

    hash_val = generate_bcrypt_hash(plaintext)
    content = path.read_text(encoding="utf-8")

    # Pola penggantian blok basicauth lama (baik aktif maupun ter-comment)
    basicauth_block = f"    basicauth * {{\n        {clean_user} {hash_val}\n    }}"

    if re.search(r"(?m)^\s*#?\s*basicauth\s+\*\s*\{", content):
        # Ganti blok yang sudah ada
        new_content = re.sub(
            r"(?m)^\s*#?\s*basicauth\s+\*\s*\{[\s\S]*?^\s*#?\s*\}",
            basicauth_block,
            content,
        )
    else:
        # Sisipkan sebelum tanda kurung penutup terakhir '}'
        last_brace = content.rfind("}")
        if last_brace != -1:
            new_content = content[:last_brace] + f"\n{basicauth_block}\n" + content[last_brace:]
        else:
            new_content = content + f"\n{basicauth_block}\n"

    _safe_write_caddyfile(path, new_content)
    reloaded = reload_caddy()
    msg = f"✅ HTTP Basic Auth berhasil DIAKTIFKAN untuk user '{clean_user}' pada {path}."
    if reloaded:
        msg += " Caddy berhasil di-reload seketika."
    return True, msg


def disable_basic_auth(caddyfile_path: Optional[Path] = None) -> Tuple[bool, str]:
    """Menonaktifkan proteksi password (portal menjadi terbuka kembali)."""
    path = caddyfile_path or get_default_caddyfile()
    if not path.exists():
        return False, f"File {path} tidak ditemukan."

    content = path.read_text(encoding="utf-8")
    # Comment out blok basicauth
    def comment_replacer(match: Any) -> str:
        lines = match.group(0).splitlines()
        return "\n".join([f"    # {l.strip()}" for l in lines])

    new_content = re.sub(
        r"(?m)^\s*basicauth\s+\*\s*\{[\s\S]*?^\s*\}",
        comment_replacer,
        content,
    )
    _safe_write_caddyfile(path, new_content)
    reloaded = reload_caddy()
    msg = f"⚠️ Basic Auth dinonaktifkan pada {path} (Portal sekarang terbuka untuk umum)."
    if reloaded:
        msg += " Caddy berhasil di-reload."
    return True, msg


def toggle_loopback_mode(private_only: bool, caddyfile_path: Optional[Path] = None) -> Tuple[bool, str]:
    """Mengubah binding port 8080 antara publik (:8080) dan lokal (127.0.0.1:8080)."""
    path = caddyfile_path or get_default_caddyfile()
    if not path.exists():
        return False, f"File {path} tidak ditemukan."

    content = path.read_text(encoding="utf-8")
    if private_only:
        new_content = re.sub(r"(?m)^\s*:8080\s*\{", "127.0.0.1:8080 {", content)
        target = "127.0.0.1:8080 (Private / SSH Tunnel Only)"
    else:
        new_content = re.sub(r"(?m)^\s*127\.0\.0\.1:8080\s*\{", ":8080 {", content)
        target = ":8080 (Public Interface)"

    _safe_write_caddyfile(path, new_content)
    reloaded = reload_caddy()
    msg = f"🔒 Binding port berhasil diubah ke {target}."
    if reloaded:
        msg += " Caddy berhasil di-reload."
    return True, msg


def main() -> None:
    parser = argparse.ArgumentParser(description="PakDir Caddy Architecture Portal Security Manager")
    subparsers = parser.add_subparsers(dest="action", required=True)

    # enable <username> <password>
    p_enable = subparsers.add_parser("enable", help="Aktifkan proteksi password Basic Auth")
    p_enable.add_argument("username", help="Username login CTO")
    p_enable.add_argument("password", help="Password login CTO")
    p_enable.add_argument("--caddyfile", help="Path khusus file Caddyfile")

    # disable
    p_disable = subparsers.add_parser("disable", help="Nonaktifkan proteksi password")
    p_disable.add_argument("--caddyfile", help="Path khusus file Caddyfile")

    # status
    p_status = subparsers.add_parser("status", help="Cek status keamanan & kredensial portal")
    p_status.add_argument("--caddyfile", help="Path khusus file Caddyfile")

    # private-loopback
    p_loopback = subparsers.add_parser("private-loopback", help="Ubah port 8080 hanya mendengarkan di 127.0.0.1 (SSH Tunnel)")
    p_loopback.add_argument("--caddyfile", help="Path khusus file Caddyfile")

    # public-bind
    p_public = subparsers.add_parser("public-bind", help="Buka binding port 8080 ke seluruh interface publik (:8080)")
    p_public.add_argument("--caddyfile", help="Path khusus file Caddyfile")

    args = parser.parse_args()
    cfile = Path(args.caddyfile) if getattr(args, "caddyfile", None) else None

    if args.action == "enable":
        ok, msg = enable_basic_auth(args.username, args.password, cfile)
        print(msg)
        sys.exit(0 if ok else 1)
    elif args.action == "disable":
        ok, msg = disable_basic_auth(cfile)
        print(msg)
        sys.exit(0 if ok else 1)
    elif args.action == "status":
        stat = get_auth_status(cfile)
        print("🛡️ STATUS KEAMANAN PORTAL ARSITEKTUR PAKDIR:")
        print(f"- File Caddyfile: {stat['caddyfile']}")
        print(f"- Binding Port: {stat['port']}")
        print(f"- Status Password: {'TERKUNCI / AKTIF' if stat['auth_enabled'] else 'TIDAK AKTIF (PUBLIK)'}")
        if stat["auth_enabled"]:
            print(f"- Username Resmi: {stat['username']}")
    elif args.action == "private-loopback":
        ok, msg = toggle_loopback_mode(private_only=True, caddyfile_path=cfile)
        print(msg)
        sys.exit(0 if ok else 1)
    elif args.action == "public-bind":
        ok, msg = toggle_loopback_mode(private_only=False, caddyfile_path=cfile)
        print(msg)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
