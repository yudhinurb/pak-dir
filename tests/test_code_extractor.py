import shutil
import tempfile
import unittest
from pathlib import Path

from orchestrator.code_extractor import (
    sanitize_rel_path,
    extract_code_blocks,
    write_extracted_code_files,
)


class TestCodeExtractor(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_extractor_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sanitize_rel_path(self) -> None:
        self.assertEqual(sanitize_rel_path("src/app.py"), "src/app.py")
        self.assertEqual(sanitize_rel_path("./src/auth/jwt.py"), "src/auth/jwt.py")
        self.assertEqual(sanitize_rel_path("`src/main.go`"), "src/main.go")
        self.assertIsNone(sanitize_rel_path("../../etc/passwd"))
        self.assertIsNone(sanitize_rel_path("../app.py"))
        self.assertIsNone(sanitize_rel_path("binary.exe"))

    def test_extract_code_blocks_various_formats(self) -> None:
        text = """
Berikut adalah rancangan modul autentikasi:

### File: src/auth/jwt.py
```python
def create_token(user_id: int) -> str:
    return f"token_{user_id}"
```

**File:** `tests/test_jwt.py`
```python
import unittest
from src.auth.jwt import create_token

class TestJWT(unittest.TestCase):
    def test_token(self):
        self.assertEqual(create_token(1), "token_1")
```

Berikut adalah file konfigurasi tambahan:
```json:config/app.json
{
  "name": "pakdir",
  "port": 8080
}
```

Dan ini script utilitas:
```bash file="scripts/run.sh"
#!/usr/bin/env bash
echo "Starting app"
```
"""
        blocks = extract_code_blocks(text)
        paths = [b["path"] for b in blocks]
        self.assertIn("src/auth/jwt.py", paths)
        self.assertIn("tests/test_jwt.py", paths)
        self.assertIn("config/app.json", paths)
        self.assertIn("scripts/run.sh", paths)

        # Cek konten salah satu block
        jwt_block = next(b for b in blocks if b["path"] == "src/auth/jwt.py")
        self.assertIn("def create_token", jwt_block["content"])

    def test_write_extracted_code_files_and_security(self) -> None:
        blocks = [
            {
                "path": "src/module.py",
                "content": "def hello():\n    return 'world'\n",
            },
            {
                "path": "tests/test_module.py",
                "content": "import unittest\n",
            },
            {
                "path": ".env",  # Dilarang!
                "content": "SECRET=leak",
            },
            {
                "path": ".git/config",  # Dilarang!
                "content": "git config",
            },
        ]
        written = write_extracted_code_files(blocks, self.temp_dir)
        written_rel = [str(f.relative_to(self.temp_dir)).replace("\\", "/") for f in written]

        self.assertIn("src/module.py", written_rel)
        self.assertIn("tests/test_module.py", written_rel)
        self.assertNotIn(".env", written_rel)
        self.assertNotIn(".git/config", written_rel)

        # Verifikasi fisik file terbentuk
        self.assertTrue((self.temp_dir / "src" / "module.py").exists())
        self.assertTrue((self.temp_dir / "tests" / "test_module.py").exists())
        self.assertFalse((self.temp_dir / ".env").exists())


if __name__ == "__main__":
    unittest.main()
