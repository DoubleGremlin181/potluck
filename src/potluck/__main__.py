"""CLI entry point for Potluck."""

import sys
from typing import NoReturn


def main() -> NoReturn:
    """Main entry point for the potluck CLI."""
    if len(sys.argv) < 2:
        print("Usage: potluck <command>")
        print("")
        print("Commands:")
        print("  mcp      Start the MCP server (stdio transport)")
        print("  web      Start the web UI server")
        print("  worker   Start the Celery worker")
        sys.exit(1)

    command = sys.argv[1]

    if command == "mcp":
        from potluck.mcp.server import run_mcp_server

        run_mcp_server()
    elif command == "web":
        from potluck.web.app import run_web_server

        run_web_server()
    elif command == "worker":
        from potluck.core.celery import run_worker

        run_worker()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
