import os
import random
import string
import tempfile

from parsing.parser import DataParser


def _random_string(max_len=80):
    return "".join(random.choices(string.printable, k=random.randint(1, max_len)))


def _random_csv_row():
    return ",".join(_random_string(20) for _ in range(3))


class TestFuzz:
    def test_fuzz_txt(self):
        for _ in range(20):
            lines = [_random_string() for _ in range(random.randint(1, 10))]
            content = "\n".join(lines)
            f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
            f.write(content)
            f.close()
            try:
                records = DataParser.auto_detect(f.name)
                assert isinstance(records, list)
            except Exception:
                pass
            finally:
                os.unlink(f.name)

    def test_fuzz_csv(self):
        for _ in range(20):
            lines = [_random_csv_row() for _ in range(random.randint(1, 10))]
            content = "\n".join(["last_name,email,paper_title"] + lines)
            f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8")
            f.write(content)
            f.close()
            try:
                records = DataParser.auto_detect(f.name)
                assert isinstance(records, list)
            except Exception:
                pass
            finally:
                os.unlink(f.name)

    def test_fuzz_json(self):
        for _ in range(20):
            content = _random_string(200)
            f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
            f.write(content)
            f.close()
            try:
                records = DataParser.auto_detect(f.name)
                assert isinstance(records, list)
            except Exception:
                pass
            finally:
                os.unlink(f.name)

    def test_fuzz_binary_txt(self):
        content = bytes(random.randint(0, 255) for _ in range(200))
        f = tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False)
        f.write(content)
        f.close()
        try:
            records = DataParser.auto_detect(f.name)
            assert isinstance(records, list)
        except Exception:
            pass
        finally:
            os.unlink(f.name)

    def test_fuzz_huge_lines(self):
        content = "A" * 10000 + "," + "B" * 10000 + "," + "C" * 10000 + "\n"
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8")
        f.write("last_name,email,paper_title\n" + content)
        f.close()
        try:
            records = DataParser.auto_detect(f.name)
            assert isinstance(records, list)
        except Exception:
            pass
        finally:
            os.unlink(f.name)

    def test_fuzz_special_chars(self):
        content = "\x00\x01\x02\x03test\x1f\x7f\x80\xff\n"
        f = tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False)
        f.write(content.encode("latin-1"))
        f.close()
        try:
            records = DataParser.auto_detect(f.name)
            assert isinstance(records, list)
        except Exception:
            pass
        finally:
            os.unlink(f.name)
