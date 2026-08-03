#!/usr/bin/env bash
#
# The backend lint gate, run by .github/workflows/lint-backend.yml.
#
# MYPY IS DELIBERATELY NOT HERE. `strict = true` is set in pyproject.toml and the
# codebase has never satisfied it — 371 errors across 59 files at the time this
# gate was first actually wired up (the workflow had been pinned to a `master`
# branch that does not exist, so it had never run). Paying that down is a real
# project, not a prerequisite for having CI, and a required check that can never
# go green just teaches people to merge past CI.
#
# Run `uv run mypy app` by hand when working on types. Put it back in this file
# the day it exits 0.
#
# ruff check + ruff format also mirror exactly what .pre-commit-config.yaml runs
# locally, so a passing pre-commit now implies a passing CI lint.

set -e
set -x

ruff check app
ruff format app --check
