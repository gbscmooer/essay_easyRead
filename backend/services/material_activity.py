"""Shared in-process activity state for current workspace material operations."""
backfill_jobs: dict[int, dict] = {}
digest_jobs: dict[int, dict] = {}
