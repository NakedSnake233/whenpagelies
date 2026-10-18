SHELL := /bin/bash
ROOT := $(CURDIR)
export XDG_CACHE_HOME := $(ROOT)/.cache
export TMPDIR := $(ROOT)/.tmp
export PIP_CACHE_DIR := $(ROOT)/.cache/pip
export PLAYWRIGHT_BROWSERS_PATH := $(ROOT)/.cache/ms-playwright

.PHONY: venv test proxy-test
venv:
	@mkdir -p logs .tmp .cache
	@nohup bash infra/setup_venv.sh > logs/venv.log 2>&1 & echo $$! > logs/venv.pid
	@echo "Environment setup launched; see logs/venv.log and logs/venv.pid"

test:
	@mkdir -p .tmp .cache
	.venv/bin/python -m pytest -q

proxy-test:
	@mkdir -p .tmp .cache
	.venv/bin/python -m pytest -q tests/test_proxy_toy.py tests/test_proxy_unit.py
