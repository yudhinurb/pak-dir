import shutil
import tempfile
import unittest
from pathlib import Path
import importlib.util

# Import set-portal-auth.py dynamically
auth_script_path = Path(__file__).resolve().parent.parent / "deploy" / "set-portal-auth.py"
spec = importlib.util.spec_from_file_location("set_portal_auth", str(auth_script_path))
set_portal_auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(set_portal_auth)


class TestSecurityHardening(unittest.TestCase):
    """
    Suite pengujian otomatis untuk memverifikasi hardening Caddyfile dan Basic Auth generator.
    """

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_caddy_"))
        self.project_root = Path(__file__).resolve().parent.parent
        self.test_caddyfile = self.temp_dir / "Caddyfile"
        shutil.copy2(self.project_root / "deploy" / "Caddyfile", self.test_caddyfile)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_bcrypt_hash_generation(self) -> None:
        raw_pass = "MySecretCt0Password!"
        hash_val = set_portal_auth.generate_bcrypt_hash(raw_pass)
        self.assertTrue(hash_val.startswith("$2"), "Hash bcrypt harus diawali dengan $2")
        self.assertGreater(len(hash_val), 40, "Panjang hash bcrypt harus valid")

    def test_02_enable_and_get_status(self) -> None:
        # Cek status sebelum diaktifkan
        init_stat = set_portal_auth.get_auth_status(self.test_caddyfile)
        self.assertFalse(init_stat["auth_enabled"])

        # Aktifkan untuk user 'cto'
        ok, msg = set_portal_auth.enable_basic_auth("cto", "SuperAdminPass2026", self.test_caddyfile)
        self.assertTrue(ok)
        self.assertIn("berhasil DIAKTIFKAN", msg)

        # Cek status setelah diaktifkan
        stat_after = set_portal_auth.get_auth_status(self.test_caddyfile)
        self.assertTrue(stat_after["auth_enabled"])
        self.assertEqual(stat_after["username"], "cto")

        # Pastikan file Caddyfile memuat blok basicauth aktif
        content = self.test_caddyfile.read_text(encoding="utf-8")
        self.assertIn("basicauth *", content)
        self.assertIn("cto $2", content)

    def test_03_disable_basic_auth(self) -> None:
        # Enable lalu disable
        set_portal_auth.enable_basic_auth("cto", "Pass123", self.test_caddyfile)
        ok, msg = set_portal_auth.disable_basic_auth(self.test_caddyfile)
        self.assertTrue(ok)

        stat = set_portal_auth.get_auth_status(self.test_caddyfile)
        self.assertFalse(stat["auth_enabled"])

    def test_04_toggle_loopback_and_public(self) -> None:
        # Ubah ke loopback (127.0.0.1:8080)
        ok, msg = set_portal_auth.toggle_loopback_mode(private_only=True, caddyfile_path=self.test_caddyfile)
        self.assertTrue(ok)
        stat = set_portal_auth.get_auth_status(self.test_caddyfile)
        self.assertTrue(stat["is_loopback"])
        self.assertEqual(stat["port"], "127.0.0.1:8080")

        # Kembalikan ke publik (:8080)
        ok_pub, msg_pub = set_portal_auth.toggle_loopback_mode(private_only=False, caddyfile_path=self.test_caddyfile)
        self.assertTrue(ok_pub)
        stat_pub = set_portal_auth.get_auth_status(self.test_caddyfile)
        self.assertFalse(stat_pub["is_loopback"])
        self.assertEqual(stat_pub["port"], ":8080")


if __name__ == "__main__":
    unittest.main()
