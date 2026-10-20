#!/usr/bin/env python3
"""Cron entry point (hermes cron --no-agent --script kyverno-watch.py): runs the contributor plugin's
watch check. Prints one line per new event; empty output stays silent."""
import importlib.util
import os
import sys

home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
plugin = os.path.join(home, "plugins", "contributor")
spec = importlib.util.spec_from_file_location("contributor", os.path.join(plugin, "__init__.py"),
                                              submodule_search_locations=[plugin])
module = importlib.util.module_from_spec(spec)
sys.modules["contributor"] = module
spec.loader.exec_module(module)
from contributor import watch  # noqa: E402

sys.exit(watch.main(sys.argv[1:]))
