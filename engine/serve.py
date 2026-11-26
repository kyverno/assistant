"""Start the engine's MCP server on stdio: `python3 engine/serve.py`.

Puts this directory first on sys.path itself, so it also works under `python -I`, which leaves the script's
directory out.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from repo_engine.server import serve  # noqa: E402

if __name__ == "__main__":
    serve()
