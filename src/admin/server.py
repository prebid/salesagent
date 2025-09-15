#!/usr/bin/env python
"""Production server entry point for Admin UI.

Supports multiple server backends:
- Waitress (production, default)
- Werkzeug (development/debugging)
- Uvicorn with ASGI adapter (alternative production)
"""

import logging
import os
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def run_waitress(app, port: int):
    """Run with Waitress WSGI server (production)."""
    try:
        from waitress import serve

        logger.info(f"Starting Admin UI with Waitress on port {port}")
        serve(app, host="0.0.0.0", port=port, threads=4)
    except ImportError:
        logger.error("Waitress not installed. Install with: pip install waitress")
        sys.exit(1)


def run_werkzeug(app, port: int, debug: bool = False):
    """Run with Werkzeug server (development)."""
    from werkzeug.serving import make_server

    logger.info(f"Starting Admin UI with Werkzeug on port {port} (debug={debug})")
    server = make_server("0.0.0.0", port, app, threaded=True)
    server.serve_forever()


def run_uvicorn_asgi(app, port: int):
    """Run with Uvicorn ASGI server via adapter."""
    try:
        import uvicorn
        from asgiref.wsgi import WsgiToAsgi

        asgi_app = WsgiToAsgi(app)
        logger.info(f"Starting Admin UI with Uvicorn on port {port}")
        uvicorn.run(asgi_app, host="0.0.0.0", port=port, log_level="info")
    except ImportError as e:
        logger.error(f"Required packages not installed: {e}")
        logger.error("Install with: pip install uvicorn asgiref")
        sys.exit(1)


def main():
    """Main entry point for the admin UI server."""
    # Initialize application with startup validation
    try:
        import sys

        sys.path.insert(0, ".")
        from src.core.startup import initialize_application

        logger.info("🚀 Initializing Admin UI...")
        initialize_application()
        logger.info("✅ Admin UI initialization completed")

    except SystemExit:
        logger.error("❌ Admin UI initialization failed - check logs")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Admin UI startup error: {e}")
        sys.exit(1)

    # Import the app factory
    from src.admin.app import create_app

    # Create the Flask app
    app, socketio = create_app()

    # Get configuration from environment
    port = int(os.environ.get("ADMIN_UI_PORT", 8001))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    server_type = os.environ.get("ADMIN_SERVER_TYPE", "waitress").lower()

    # Force production settings for security
    if not debug:
        os.environ.pop("WERKZEUG_SERVER_FD", None)
        os.environ["FLASK_ENV"] = "production"
        os.environ["FLASK_DEBUG"] = "0"
        os.environ["WERKZEUG_DEBUG_PIN"] = "off"

    # Select and run the appropriate server
    if debug or server_type == "werkzeug":
        run_werkzeug(app, port, debug)
    elif server_type == "uvicorn":
        run_uvicorn_asgi(app, port)
    else:  # Default to waitress
        run_waitress(app, port)


if __name__ == "__main__":
    main()
