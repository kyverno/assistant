"""The repository engine: PR and issue fetch, the review queue ranked in code, and the merge-sequencing graph.

No Hermes imports. `server.py` serves TOOLS over MCP (stdio); any MCP client, Hermes included, uses the same tools.
"""

from __future__ import annotations

from .fetch_tools import (
    FETCH_FILE_DIFF_OVERLAP_SCHEMA,
    FETCH_ISSUE_CANDIDATES_SCHEMA,
    FETCH_ISSUE_DETAILS_SCHEMA,
    FETCH_PR_CANDIDATES_SCHEMA,
    FETCH_PR_DETAILS_SCHEMA,
    handle_fetch_file_diff_overlap,
    handle_fetch_issue_candidates,
    handle_fetch_issue_details,
    handle_fetch_pr_candidates,
    handle_fetch_pr_details,
)
from .pack_tools import REPO_PACK_SCHEMA, handle_repo_pack
from .queue_tools import ITEM_SCHEMA, QUEUE_SCHEMA, handle_item, handle_queue
from .sequence_tools import SEQUENCE_PRS_SCHEMA, handle_sequence_prs

# (schema, handler). Each schema is {"name", "description", "parameters"}; each handler takes the arguments and
# returns a JSON string.
TOOLS = [
    (QUEUE_SCHEMA, handle_queue),
    (ITEM_SCHEMA, handle_item),
    (FETCH_PR_CANDIDATES_SCHEMA, handle_fetch_pr_candidates),
    (FETCH_PR_DETAILS_SCHEMA, handle_fetch_pr_details),
    (FETCH_ISSUE_CANDIDATES_SCHEMA, handle_fetch_issue_candidates),
    (FETCH_ISSUE_DETAILS_SCHEMA, handle_fetch_issue_details),
    (FETCH_FILE_DIFF_OVERLAP_SCHEMA, handle_fetch_file_diff_overlap),
    (SEQUENCE_PRS_SCHEMA, handle_sequence_prs),
    (REPO_PACK_SCHEMA, handle_repo_pack),
]
