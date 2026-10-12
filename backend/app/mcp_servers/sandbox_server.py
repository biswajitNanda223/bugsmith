"""MCP server exposing the patch-validation sandbox to agents.

Run:  python -m app.mcp_servers.sandbox_server   (stdio)
"""
from mcp.server.fastmcp import FastMCP

from app import sandbox

mcp = FastMCP("bugsmith-sandbox")


@mcp.tool()
def validate_patch(patch: str, repro_test: str, workdir: str = ".", test_dir: str = "") -> dict:
    """Validate a candidate fix in a throwaway copy of the repo.

    Steps (stops at first failure): repro test fails before the patch -> patch applies ->
    repro test passes after -> existing tests pass.
    patch: unified diff, git style (a/ b/ prefixes). repro_test: pytest source that reproduces the crash.
    workdir: subdirectory used as the import root (e.g. 'demo-app'). test_dir: existing suite path relative to workdir.
    """
    return sandbox.validate_patch(patch, repro_test, workdir, test_dir).to_dict()


if __name__ == "__main__":
    mcp.run()
