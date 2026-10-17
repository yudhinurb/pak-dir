import io
import shutil
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter
from orchestrator.file_processor import (
    is_supported_file,
    sanitize_filename,
    extract_text_from_code_or_text,
    extract_text_from_pdf,
    save_uploaded_file,
    process_single_attachment,
    format_attachments_context,
)


class TestFileProcessor(unittest.TestCase):
    """
    Suite pengujian untuk memverifikasi fungsionalitas pemrosesan file teks dan dokumen PDF.
    """

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_uploads_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_supported_files_and_sanitization(self) -> None:
        self.assertTrue(is_supported_file("document.pdf"))
        self.assertTrue(is_supported_file("schema.sql"))
        self.assertTrue(is_supported_file("app.py"))
        self.assertTrue(is_supported_file("config.json"))
        self.assertTrue(is_supported_file("notes.md"))
        self.assertFalse(is_supported_file("malicious.exe"))
        self.assertFalse(is_supported_file("archive.zip"))

        clean = sanitize_filename("../../etc/passwd;rm -rf.pdf")
        self.assertNotIn("/", clean)
        self.assertNotIn(";", clean)
        self.assertTrue(clean.endswith(".pdf"))

    def test_02_text_extraction(self) -> None:
        code_bytes = b"def test_handler():\n    return {'status': 200}\n"
        ok, text = extract_text_from_code_or_text(code_bytes)
        self.assertTrue(ok)
        self.assertIn("test_handler", text)

        # Uji batas pemotongan teks
        long_bytes = b"A" * 70000
        ok, text_long = extract_text_from_code_or_text(long_bytes, max_chars=1000)
        self.assertTrue(ok)
        self.assertIn("Peringatan: Teks dipotong", text_long)
        self.assertLess(len(text_long), 2000)

    def test_03_pdf_creation_and_extraction(self) -> None:
        # Buat dokumen PDF valid di memori menggunakan pypdf
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        
        pdf_stream = io.BytesIO()
        writer.write(pdf_stream)
        raw_pdf = pdf_stream.getvalue()

        # Ekstraksi PDF kosong/blank
        ok, content, pages = extract_text_from_pdf(raw_pdf)
        self.assertTrue(ok)
        self.assertEqual(pages, 1)

    def test_04_save_and_process_single_attachment(self) -> None:
        # 1. File teks valid
        content_bytes = b"CTO Mandate: Build autonomous agent swarm\nPriority: High"
        res = process_single_attachment(
            filename="mandate.txt",
            raw_bytes=content_bytes,
            upload_dir=self.temp_dir,
        )
        self.assertTrue(res["success"])
        self.assertIn("Build autonomous agent swarm", res["content"])
        self.assertTrue(Path(res["saved_path"]).exists())

        # 2. File melebihi batas ukuran (simulasi > 15 MB)
        from orchestrator import file_processor
        original_limit = file_processor.MAX_FILE_SIZE_BYTES
        try:
            file_processor.MAX_FILE_SIZE_BYTES = 100  # Set limit rendah untuk test
            oversize_res = process_single_attachment(
                filename="big.txt",
                raw_bytes=b"X" * 200,
                upload_dir=self.temp_dir,
            )
            self.assertFalse(oversize_res["success"])
            self.assertIn("melebihi batas", oversize_res["error"])
        finally:
            file_processor.MAX_FILE_SIZE_BYTES = original_limit

        # 3. Format tidak didukung
        bin_res = process_single_attachment(
            filename="driver.bin",
            raw_bytes=b"\x00\x01\x02",
            upload_dir=self.temp_dir,
        )
        self.assertFalse(bin_res["success"])
        self.assertIn("belum didukung", bin_res["error"])

    def test_05_format_attachments_context(self) -> None:
        items = [
            {
                "filename": "spec.md",
                "success": True,
                "size_kb": 1.2,
                "pages": 1,
                "is_pdf": False,
                "content": "# Feature Spec\nEndpoint /api/users",
            },
            {
                "filename": "corrupted.pdf",
                "success": False,
                "error": "File korup",
            },
        ]
        ctx_block = format_attachments_context(items)
        self.assertIn("DOKUMEN & BERKAS TERLAMPIR", ctx_block)
        self.assertIn("spec.md", ctx_block)
        self.assertIn("/api/users", ctx_block)
        self.assertIn("Gagal diekstrak", ctx_block)


if __name__ == "__main__":
    unittest.main()
