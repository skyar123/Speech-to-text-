.PHONY: help install dev test lint fmt serve demo clean

VENV ?= .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

$(VENV):
	python3 -m venv $(VENV)

install: $(VENV) ## Install ttskit with the server and ffmpeg extras
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[server,ffmpeg]"

dev: $(VENV) ## Install everything, including dev tooling
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[server,ffmpeg,docs,dev]"

test: ## Run the test suite (no network required)
	$(PY) -m pytest -q

lint: ## Check formatting and lint rules
	$(VENV)/bin/ruff check .

fmt: ## Apply safe lint fixes
	$(VENV)/bin/ruff check . --fix

serve: ## Start the OpenAI-compatible server on :5050
	$(VENV)/bin/ttskit serve

demo: ## Narrate the bundled sample document
	$(VENV)/bin/ttskit book examples/sample.md -o out/sample.mp3 --subs srt,vtt --chapters

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache out
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
