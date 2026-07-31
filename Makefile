.PHONY: test lint typecheck security clean doctor docs site sbom all

test:
	python -m pytest tests/ -v --cov --cov-report=term --cov-report=html

lint:
	ruff check .

typecheck:
	mypy core/ engine/ parsing/ exports/ run.py --ignore-missing-imports --explicit-package-bases

security:
	bandit -r core/ engine/ parsing/ exports/ run.py -x tests -c pyproject.toml

doctor:
	python run.py --doctor

docs:
	.venv/bin/mkdocs build 2>/dev/null || mkdocs build

site:
	.venv/bin/mkdocs serve 2>/dev/null || mkdocs serve

sbom:
	python -m cyclonedx_py --pyproject pyproject.toml --output-format json --output sbom.json 2>/dev/null || cyclonedx-py --pyproject pyproject.toml --output-format json --output sbom.json

dist:
	python -m build

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete
	find . -type f -name '*.pyo' -delete
	find . -type f -name '.coverage' -delete
	find . -type d -name 'htmlcov' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '.pytest_cache' -exec rm -rf {} + 2>/dev/null || true
	rm -rf exports/ sent/ scheduler/ logs/ data/ .mypy_cache .ruff_cache site/

all: lint typecheck test security doctor
