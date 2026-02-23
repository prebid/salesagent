"""A2A Server integration for Prebid Sales Agent.

The A2A server is now integrated into the unified FastAPI app (src/app.py).
Key exports: AdCPRequestHandler (request handling), create_agent_card (discovery).
"""

from .adcp_a2a_server import AdCPRequestHandler, create_agent_card

__all__ = ["AdCPRequestHandler", "create_agent_card"]
