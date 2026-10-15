.PHONY: setup lint fmt test check ingest status

setup:          ## create venv and install core + dev deps
	uv sync --group dev

setup-audio:    ## add audio analysis deps (Milestone 4)
	uv sync --group dev --extra audio

setup-ui:       ## add review UI deps (Milestone 5)
	uv sync --group dev --extra ui

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest

check: lint test

ingest:
	uv run playlistsort ingest

status:
	uv run playlistsort status --coverage
