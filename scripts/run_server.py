#!/usr/bin/env python3
"""Run the AdCP Sales Agent with HTTP transport.

Starts the unified FastAPI application via uvicorn, serving MCP, A2A,
and Admin UI from a single process.
"""

import os
import sys


def main():
    """Run the server with configurable port."""
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

    port = int(os.environ.get("ADCP_SALES_PORT", "8080"))
    host = os.environ.get("ADCP_SALES_HOST", "0.0.0.0")

    # Check if we're in production (Docker or Fly.io)
    is_production = bool(os.environ.get("FLY_APP_NAME") or os.environ.get("PRODUCTION"))

    if is_production:
        # In production, bind to all interfaces
        host = "0.0.0.0"

    print(f"Starting AdCP Sales Agent on {host}:{port}")
    print(f"Server endpoint: http://{host}:{port}/")

    import uvicorn

    try:
        # proxy_headers=True + forwarded_allow_ips are REQUIRED behind nginx/Fly edge:
        # - request.client.host returns real client IP (for SlowAPI rate limiting, audit logs)
        # - request.url.scheme returns "https" (for OAuth redirect URI generation, CSRF Origin
        #   validation, HSTS emission by SecurityHeadersMiddleware)
        # FORWARDED_ALLOW_IPS env var overrides default "*" for edge-exposed deployments
        # (set to nginx's CIDR range or single IP in production behind untrusted networks).
        uvicorn.run(
            "src.app:app",
            host=host,
            port=port,
            log_level="info",
            proxy_headers=True,
            forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "*"),
        )
    except KeyboardInterrupt:
        print("\nServer stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
