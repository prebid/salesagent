#!/usr/bin/env python3
"""Run the AdCP Sales Agent with HTTP transport.

Starts the unified FastAPI application via uvicorn, serving MCP, A2A,
and Admin UI from a single process.
"""

import argparse
import sys


def main():
    """Run the server with configurable port."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=None, help="Listen port (default: ADCP_SALES_PORT).")
    parser.add_argument("--host", default=None, help="Bind address (default: ADCP_SALES_HOST).")
    args = parser.parse_args()

    # Initialize application with startup validation
    try:
        # Add current directory to path for imports
        sys.path.insert(0, ".")
        from src.core.startup import initialize_application

        print("Initializing AdCP Sales Agent...")
        initialize_application()
        print("Application initialization completed")

    except SystemExit:
        print("Application initialization failed - check logs")
        sys.exit(1)
    except Exception as e:
        print(f"Startup error: {e}")
        sys.exit(1)

    # initialize_application read the environment; this is the same object.
    from src.core.config import get_settings

    runtime = get_settings().runtime
    port = args.port if args.port is not None else runtime.adcp_sales_port
    host = args.host if args.host is not None else runtime.adcp_sales_host

    if runtime.is_production:
        # In production, bind to all interfaces
        host = "0.0.0.0"

    print(f"Starting AdCP Sales Agent on {host}:{port}")
    print(f"Server endpoint: http://{host}:{port}/")

    import uvicorn

    try:
        uvicorn.run("src.app:app", host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        print("\nServer stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
