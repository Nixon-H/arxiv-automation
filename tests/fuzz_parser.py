import random
import string
import tempfile
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from parsing.parser import DataParser


def random_text(size: int = 200) -> str:
    return "".join(random.choices(string.printable, k=size))


def random_binary(size: int = 200) -> bytes:
    return bytes(random.randint(0, 255) for _ in range(size))


def test_fuzz_txt():
    for _ in range(100):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write(random_text())
            path = f.name
        try:
            DataParser.parse_txt(path)
        except Exception:
            pass
        finally:
            os.unlink(path)


def test_fuzz_csv():
    for _ in range(100):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            f.write(random_text())
            path = f.name
        try:
            DataParser.parse_csv(path)
        except Exception:
            pass
        finally:
            os.unlink(path)


def test_fuzz_json():
    for _ in range(100):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write(random_text())
            path = f.name
        try:
            DataParser.parse_json(path)
        except Exception:
            pass
        finally:
            os.unlink(path)


def test_fuzz_binary():
    for _ in range(50):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="wb") as f:
            f.write(random_binary())
            path = f.name
        try:
            DataParser.parse_txt(path)
        except Exception:
            pass
        finally:
            os.unlink(path)


if __name__ == "__main__":
    test_fuzz_txt()
    test_fuzz_csv()
    test_fuzz_json()
    test_fuzz_binary()
    print("All fuzz tests passed (no crashes)")