import shutil
import tempfile
import unittest
from pathlib import Path

from orchestrator.qa_runner import check_python_syntax, run_real_qa


class TestQARunner(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_qa_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_syntax_check_valid_and_invalid(self) -> None:
        good_file = self.temp_dir / "good.py"
        good_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

        bad_file = self.temp_dir / "bad.py"
        bad_file.write_text("def add(a, b\n    return a + b\n", encoding="utf-8")

        code_good, _ = check_python_syntax([good_file])
        self.assertEqual(code_good, 0)

        code_bad, msg_bad = check_python_syntax([bad_file])
        self.assertEqual(code_bad, 1)
        self.assertIn("SyntaxError", msg_bad)

    def test_02_run_real_qa_with_failing_and_passing_test(self) -> None:
        src_file = self.temp_dir / "calculator.py"
        src_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

        fail_test = self.temp_dir / "test_calculator_fail.py"
        fail_test.write_text(
            "import unittest\nfrom calculator import add\n\n"
            "class TestCalc(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(1, 1), 999)\n",
            encoding="utf-8",
        )

        # Harus gagal
        code_fail, out_fail = run_real_qa(self.temp_dir, written_files=[src_file, fail_test])
        self.assertNotEqual(code_fail, 0)
        self.assertIn("FAILED", out_fail)

        # Perbaiki test
        pass_test = self.temp_dir / "test_calculator_pass.py"
        pass_test.write_text(
            "import unittest\nfrom calculator import add\n\n"
            "class TestCalc(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(1, 1), 2)\n",
            encoding="utf-8",
        )

        # Harus lulus
        code_pass, out_pass = run_real_qa(self.temp_dir, written_files=[src_file, pass_test])
        self.assertEqual(code_pass, 0)
        self.assertIn("PASSED", out_pass)


if __name__ == "__main__":
    unittest.main()
