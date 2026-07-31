import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def sample_txt():
    return """\
Dr. Alice Smith, alice@mit.edu, Quantum Computing
Prof. Bob Jones, bob@stanford.edu, ML Theory
Dr. Carol Lee, carol@berkeley.edu, NLP
"""


@pytest.fixture
def sample_csv():
    return """\
last_name,email,paper_title
Smith,alice@mit.edu,Quantum Computing
Jones,bob@stanford.edu,ML Theory
"""


@pytest.fixture
def sample_json():
    return """\
[
  {"last_name": "Smith", "email": "alice@mit.edu", "paper_title": "Quantum Computing"},
  {"last_name": "Jones", "email": "bob@stanford.edu", "paper_title": "ML Theory"}
]
"""


@pytest.fixture
def sample_yaml():
    return """\
- last_name: Smith
  email: alice@mit.edu
  paper_title: Quantum Computing
- last_name: Jones
  email: bob@stanford.edu
  paper_title: ML Theory
"""


@pytest.fixture
def temp_file(request):
    """Create a temp file with content, clean up after."""
    content, suffix = request.param
    f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    yield f.name
    os.unlink(f.name)


@pytest.fixture
def txt_file(sample_txt):
    return _make_temp(sample_txt, ".txt")


@pytest.fixture
def csv_file(sample_csv):
    return _make_temp(sample_csv, ".csv")


@pytest.fixture
def json_file(sample_json):
    return _make_temp(sample_json, ".json")


@pytest.fixture
def yaml_file(sample_yaml):
    return _make_temp(sample_yaml, ".yaml")


def _make_temp(content, suffix):
    import tempfile
    f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


@pytest.fixture(autouse=True)
def cleanup_temp(request):
    yield
    for attr in ("txt_file", "csv_file", "json_file", "yaml_file"):
        val = getattr(request.node, "fixtureresults", None)
        if val:
            p = val.get(attr)
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


@pytest.fixture(scope="session")
def template_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("templates")
    (d / "template.txt").write_text("Dear {{ last_name }},\n{{ paper_title }}\nBest,\n{{ your_name }}")
    (d / "template.html").write_text("<html><body>Dear {{ last_name }},<br>{{ paper_title }}</body></html>")
    return d
