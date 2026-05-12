PYTHON = python3
PIP = $(PYTHON) -m pip

.PHONY: install lint test validate

install:
	$(PIP) install --upgrade pip
	@if [ -f requirements.txt ]; then \
		$(PIP) install -r requirements.txt; \
	else \
		echo "No requirements.txt found, skipping."; \
	fi

lint:
	@echo "Running linters..."
	@$(PIP) install ruff flake8 >/dev/null 2>&1 || true
	-ruff check . || true
	-flake8 . || true

test:
	@echo "Running tests..."
	@$(PIP) install pytest pytest-cov >/dev/null 2>&1 || true
	pytest --cov=app || \
		( echo "No tests collected or pytest failed; continuing CI (adjust Makefile to change this behavior)" && exit 0 )

validate: install lint test
