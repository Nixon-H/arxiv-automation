.PHONY: test lint typecheck security clean doctor all

test:
	python -m pytest tests/ -v --cov --cov-report=term --cov-report=html

lint:
	ruff check .

typecheck:
	mypy core/ engine/ parsing/ exports/ run.py --ignore-missing-imports

security:
	bandit -r core/ engine/ parsing/ exports/ run.py -x tests

doctor:
	python run.py --doctor

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete
	find . -type f -name '*.pyo' -delete
	find . -type f -name '.coverage' -delete
	find . -type d -name 'htmlcov' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '.pytest_cache' -exec rm -rf {} + 2>/dev/null || true
	rm -rf exports/ sent/ scheduler/ logs/ data/ .mypy_cache .ruff_cache

all: lint typecheck test security doctor
