"""The engine: pure computation over fetched PRs and issues, with no GitHub calls and no Hermes imports.

profile.py  what a PR changes: lines per file class, functions touched, tests, generated output, docs
owners.py   CODEOWNERS (a copy of the contributor plugin's parser until the engine moves to the top level)
rank.py     priority, readiness, risk and close candidates, each with its reasons
layers.py   the queue (L1), card (L2) and code (L3) views, each kept under its character budget
"""
