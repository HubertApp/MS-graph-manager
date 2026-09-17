PYTHON = python3
PIP = $(PYTHON) -m pip

.PHONY: install lint test validate lock

install:
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

lint:
	ruff check .

# --cov-fail-under fait échouer la cible sous le seuil : c'est ce qui rend la
# couverture opposable plutôt qu'informative. Le rapport XML part en artefact de CI.
test:
	pytest --cov=app --cov-report=term-missing --cov-report=xml --cov-fail-under=90

validate: install lint test

# Recompile les versions épinglées après modification des fichiers .in.
# Exécuté dans l'image Python du service pour que la résolution corresponde à
# l'environnement d'exécution réel, et non à l'interpréteur du poste.
lock:
	docker run --rm -v "$(CURDIR)":/w -w /w python:3.12-slim-bookworm sh -c "\
		pip install --quiet --upgrade pip pip-tools && \
		pip-compile --quiet --strip-extras --output-file=requirements.txt requirements.in && \
		pip-compile --quiet --strip-extras --output-file=requirements-dev.txt requirements-dev.in && \
		chown $$(id -u):$$(id -g) requirements.txt requirements-dev.txt"
