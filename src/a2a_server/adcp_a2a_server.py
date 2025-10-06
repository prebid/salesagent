#!/usr/bin/env python3
"""
AdCP Sales Agent A2A Server using official a2a-sdk library.
Supports both standard A2A message format and JSON-RPC 2.0.
"""

import logging
import os
import sys
import threading
import uuid
from collections.abc import AsyncGenerator
from typing import Any

# Fix import order to avoid local a2a directory conflict
# Import official a2a-sdk first before adding local paths

original_path = sys.path.copy()

# Temporarily remove current directory to avoid local a2a conflict
if "" in sys.path:
    sys.path.remove("")
if "." in sys.path:
    sys.path.remove(".")

# Official a2a-sdk imports (must be before adding local paths)
from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import (
    AgentCard,
    Artifact,
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    Message,
    MessageSendParams,
    MethodNotFoundError,
    Part,
    Task,
    TaskIdParams,
    TaskQueryParams,
    TaskState,
    TaskStatus,
)
from a2a.utils.errors import ServerError

# Restore paths and add parent directories for local imports
sys.path = original_path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Import core functions for direct calls (raw functions without FastMCP decorators)
from datetime import UTC, datetime

from src.core.audit_logger import get_audit_logger
from src.core.auth_utils import get_principal_from_token
from src.core.config_loader import get_current_tenant
from src.core.schemas import (
    GetSignalsRequest,
    ListAuthorizedPropertiesRequest,
)
from src.core.testing_hooks import TestingContext
from src.core.tool_context import ToolContext
from src.core.tools import (
    create_media_buy_raw as core_create_media_buy_tool,
)
from src.core.tools import (
    get_media_buy_delivery_raw as core_get_media_buy_delivery_tool,
)
from src.core.tools import (
    get_products_raw as core_get_products_tool,
)
from src.core.tools import (
    get_signals_raw as core_get_signals_tool,
)
from src.core.tools import (
    list_authorized_properties_raw as core_list_authorized_properties_tool,
)
from src.core.tools import (
    list_creative_formats_raw as core_list_creative_formats_tool,
)
from src.core.tools import (
    list_creatives_raw as core_list_creatives_tool,
)
from src.core.tools import (
    sync_creatives_raw as core_sync_creatives_tool,
)
from src.core.tools import (
    update_media_buy_raw as core_update_media_buy_tool,
)
from src.core.tools import (
    update_performance_index_raw as core_update_performance_index_tool,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Thread-local storage for current request auth token
_request_context = threading.local()


class AdCPRequestHandler(RequestHandler):
    """Request handler for AdCP A2A operations supporting JSON-RPC 2.0."""

    def __init__(self):
        """Initialize the AdCP A2A request handler."""
        self.tasks = {}  # In-memory task storage
        logger.info("AdCP Request Handler initialized for direct function calls")

    def _get_auth_token(self) -> str | None:
        """Extract Bearer token from current request context."""
        return getattr(_request_context, "auth_token", None)

    def _create_tool_context_from_a2a(self, auth_token: str, tool_name: str, context_id: str = None) -> ToolContext:
        """Create a ToolContext from A2A authentication information.

        Args:
            auth_token: Bearer token from Authorization header
            tool_name: Name of the tool being called
            context_id: Optional context ID for conversation tracking

        Returns:
            ToolContext for calling core functions

        Raises:
            ValueError: If authentication fails
        """
        # Get request headers for debugging
        headers = getattr(_request_context, "request_headers", {})
        apx_host = headers.get("apx-incoming-host", "NOT_PRESENT")

        # Authenticate using the token
        principal_id = get_principal_from_token(auth_token)
        if not principal_id:
            raise ServerError(
                InvalidRequestError(
                    message=f"Invalid authentication token (not found in database). "
                    f"Token: {auth_token[:20]}..., "
                    f"Apx-Incoming-Host: {apx_host}"
                )
            )

        # Get tenant info (set as side effect of authentication)
        tenant = get_current_tenant()
        if not tenant:
            raise ServerError(
                InvalidRequestError(
                    message=f"Unable to determine tenant from authentication. "
                    f"Principal: {principal_id}, "
                    f"Apx-Incoming-Host: {apx_host}"
                )
            )

        # Generate context ID if not provided
        if not context_id:
            context_id = f"a2a_{datetime.now(UTC).timestamp()}"

        # Create ToolContext
        return ToolContext(
            context_id=context_id,
            tenant_id=tenant["tenant_id"],
            principal_id=principal_id,
            tool_name=tool_name,
            request_timestamp=datetime.now(UTC),
            metadata={"source": "a2a_server", "protocol": "a2a_jsonrpc"},
            testing_context=TestingContext().model_dump(),  # Default testing context for A2A requests
        )

    def _log_a2a_operation(
        self,
        operation: str,
        tenant_id: str,
        principal_id: str,
        success: bool = True,
        details: dict = None,
        error: str = None,
    ):
        """Log A2A operations to audit system for visibility in activity feed."""
        try:
            if not tenant_id:
                return

            audit_logger = get_audit_logger("A2A", tenant_id)
            audit_logger.log_operation(
                operation=operation,
                principal_name=f"A2A_Client_{principal_id}",
                principal_id=principal_id,
                adapter_id="a2a_client",
                success=success,
                details=details,
                error=error,
                tenant_id=tenant_id,
            )
        except Exception as e:
            logger.warning(f"Failed to log A2A operation: {e}")

    async def on_message_send(
        self,
        params: MessageSendParams,
        context: ServerCallContext | None = None,
    ) -> Task | Message:
        """Handle 'message/send' method for non-streaming requests.

        Supports both invocation patterns from AdCP PR #48:
        1. Natural Language: parts[{kind: "text", text: "..."}]
        2. Explicit Skill: parts[{kind: "data", data: {skill: "...", parameters: {...}}}]

        Args:
            params: Parameters including the message and configuration
            context: Server call context

        Returns:
            Task object or Message response
        """
        logger.info(f"Handling message/send request: {params}")

        # Parse message for both text and structured data parts
        message = params.message
        text_parts = []
        skill_invocations = []

        if hasattr(message, "parts") and message.parts:
            for part in message.parts:
                # Handle text parts (natural language invocation)
                if hasattr(part, "text"):
                    text_parts.append(part.text)
                elif hasattr(part, "root") and hasattr(part.root, "text"):
                    text_parts.append(part.root.text)

                # Handle structured data parts (explicit skill invocation)
                elif hasattr(part, "data") and isinstance(part.data, dict):
                    # Support both "input" (A2A spec) and "parameters" (legacy) for skill params
                    if "skill" in part.data:
                        params_data = part.data.get("input") or part.data.get("parameters", {})
                        skill_invocations.append({"skill": part.data["skill"], "parameters": params_data})
                        logger.info(
                            f"Found explicit skill invocation: {part.data['skill']} with params: {list(params_data.keys())}"
                        )

                # Handle nested data structure (some A2A clients use this format)
                elif hasattr(part, "root") and hasattr(part.root, "data"):
                    data = part.root.data
                    if isinstance(data, dict) and "skill" in data:
                        # Support both "input" (A2A spec) and "parameters" (legacy) for skill params
                        params_data = data.get("input") or data.get("parameters", {})
                        skill_invocations.append({"skill": data["skill"], "parameters": params_data})
                        logger.info(
                            f"Found explicit skill invocation (nested): {data['skill']} with params: {list(params_data.keys())}"
                        )

        # Combine text for natural language fallback
        combined_text = " ".join(text_parts).strip().lower()

        # Create task for tracking
        task_id = f"task_{len(self.tasks) + 1}"
        # Handle message_id being a number or string
        msg_id = str(params.message.message_id) if hasattr(params.message, "message_id") else None
        context_id = params.message.context_id or msg_id or f"ctx_{task_id}"

        # Prepare task metadata with both invocation types
        task_metadata = {
            "request_text": combined_text,
            "invocation_type": "explicit_skill" if skill_invocations else "natural_language",
        }
        if skill_invocations:
            task_metadata["skills_requested"] = [inv["skill"] for inv in skill_invocations]

        task = Task(
            id=task_id,
            context_id=context_id,
            kind="task",
            status=TaskStatus(state=TaskState.working),
            metadata=task_metadata,
        )
        self.tasks[task_id] = task

        try:
            # Get authentication token
            auth_token = self._get_auth_token()
            if not auth_token:
                raise ServerError(
                    InvalidRequestError(
                        message="Missing authentication token - Bearer token required in Authorization header"
                    )
                )

            # Route: Handle explicit skill invocations first, then natural language fallback
            if skill_invocations:
                # Process explicit skill invocations
                results = []
                for invocation in skill_invocations:
                    skill_name = invocation["skill"]
                    parameters = invocation["parameters"]
                    logger.info(f"Processing explicit skill: {skill_name} with parameters: {parameters}")

                    try:
                        result = await self._handle_explicit_skill(skill_name, parameters, auth_token)
                        results.append({"skill": skill_name, "result": result, "success": True})
                    except ServerError:
                        # ServerError should bubble up immediately (JSON-RPC error)
                        raise
                    except Exception as e:
                        logger.error(f"Error in explicit skill {skill_name}: {e}")
                        results.append({"skill": skill_name, "error": str(e), "success": False})

                # Create artifacts for all skill results
                for i, res in enumerate(results):
                    artifact_data = res["result"] if res["success"] else {"error": res["error"]}
                    task.artifacts = task.artifacts or []
                    task.artifacts.append(
                        Artifact(
                            artifactId=f"skill_result_{i+1}",
                            name=f"{'error' if not res['success'] else res['skill']}_result",
                            parts=[Part(type="data", data=artifact_data)],
                        )
                    )

                # Check if any skills failed and determine task status
                failed_skills = [res["skill"] for res in results if not res["success"]]
                successful_skills = [res["skill"] for res in results if res["success"]]

                if failed_skills and not successful_skills:
                    # All skills failed - mark task as failed
                    task.status = TaskStatus(state=TaskState.failed)
                    return task
                elif successful_skills:
                    # Log successful skill invocations
                    try:
                        tool_context = self._create_tool_context_from_a2a(auth_token, successful_skills[0])
                        self._log_a2a_operation(
                            "explicit_skill_invocation",
                            tool_context.tenant_id,
                            tool_context.principal_id,
                            True,
                            {"skills": successful_skills, "count": len(successful_skills)},
                        )
                    except Exception as e:
                        logger.warning(f"Could not log skill invocations: {e}")

            # Natural language fallback (existing keyword-based routing)
            elif any(word in combined_text for word in ["product", "inventory", "available", "catalog"]):
                result = await self._get_products(combined_text, auth_token)
                # Extract tenant and principal for logging
                try:
                    tool_context = self._create_tool_context_from_a2a(auth_token, "get_products")
                    tenant_id = tool_context.tenant_id
                    principal_id = tool_context.principal_id
                except Exception as e:
                    logger.warning(f"Could not extract context for logging: {e}")
                    tenant_id = "unknown"
                    principal_id = "unknown"

                self._log_a2a_operation(
                    "get_products",
                    tenant_id,
                    principal_id,
                    True,
                    {
                        "query": combined_text[:100],
                        "product_count": len(result.get("products", [])) if isinstance(result, dict) else 0,
                    },
                )
                task.artifacts = [
                    Artifact(
                        artifactId="product_catalog_1", name="product_catalog", parts=[Part(type="data", data=result)]
                    )
                ]
            elif any(word in combined_text for word in ["price", "pricing", "cost", "cpm", "budget"]):
                result = self._get_pricing()
                # Extract tenant and principal for logging
                try:
                    tool_context = self._create_tool_context_from_a2a(auth_token, "get_pricing")
                    tenant_id = tool_context.tenant_id
                    principal_id = tool_context.principal_id
                except Exception as e:
                    logger.warning(f"Could not extract context for logging: {e}")
                    tenant_id = "unknown"
                    principal_id = "unknown"

                self._log_a2a_operation(
                    "get_pricing",
                    tenant_id,
                    principal_id,
                    True,
                    {
                        "query": combined_text[:100],
                        "pricing_models": len(result.get("pricing_models", [])) if isinstance(result, dict) else 0,
                    },
                )
                task.artifacts = [
                    Artifact(
                        artifactId="pricing_info_1", name="pricing_information", parts=[Part(type="data", data=result)]
                    )
                ]
            elif any(word in combined_text for word in ["target", "audience"]):
                result = self._get_targeting()
                # Extract tenant and principal for logging
                try:
                    tool_context = self._create_tool_context_from_a2a(auth_token, "get_targeting")
                    tenant_id = tool_context.tenant_id
                    principal_id = tool_context.principal_id
                except Exception as e:
                    logger.warning(f"Could not extract context for logging: {e}")
                    tenant_id = "unknown"
                    principal_id = "unknown"

                self._log_a2a_operation(
                    "get_targeting",
                    tenant_id,
                    principal_id,
                    True,
                    {
                        "query": combined_text[:100],
                        "targeting_categories": (
                            len(result.get("targeting_options", {})) if isinstance(result, dict) else 0
                        ),
                    },
                )
                task.artifacts = [
                    Artifact(
                        artifactId="targeting_opts_1", name="targeting_options", parts=[Part(type="data", data=result)]
                    )
                ]
            elif any(word in combined_text for word in ["create", "buy", "campaign", "media"]):
                result = await self._create_media_buy(combined_text, auth_token)
                # Extract tenant and principal for logging
                try:
                    tool_context = self._create_tool_context_from_a2a(auth_token, "create_media_buy")
                    tenant_id = tool_context.tenant_id
                    principal_id = tool_context.principal_id
                except Exception as e:
                    logger.warning(f"Could not extract context for logging: {e}")
                    tenant_id = "unknown"
                    principal_id = "unknown"

                self._log_a2a_operation(
                    "create_media_buy",
                    tenant_id,
                    principal_id,
                    result.get("success", False),
                    {"query": combined_text[:100], "success": result.get("success", False)},
                    result.get("message") if not result.get("success") else None,
                )
                if result.get("success"):
                    task.artifacts = [
                        Artifact(
                            artifactId="media_buy_1", name="media_buy_created", parts=[Part(type="data", data=result)]
                        )
                    ]
                else:
                    task.artifacts = [
                        Artifact(
                            artifactId="media_buy_error_1",
                            name="media_buy_error",
                            parts=[Part(type="data", data=result)],
                        )
                    ]
            else:
                # General help response
                capabilities = {
                    "supported_queries": [
                        "product_catalog",
                        "targeting_options",
                        "pricing_information",
                        "campaign_creation",
                    ],
                    "example_queries": [
                        "What video ad products do you have available?",
                        "Show me targeting options",
                        "What are your pricing models?",
                        "How do I create a media buy?",
                    ],
                }
                # Extract tenant and principal for logging
                try:
                    tool_context = self._create_tool_context_from_a2a(auth_token, "get_capabilities")
                    tenant_id = tool_context.tenant_id
                    principal_id = tool_context.principal_id
                except Exception as e:
                    logger.warning(f"Could not extract context for logging: {e}")
                    tenant_id = "unknown"
                    principal_id = "unknown"

                self._log_a2a_operation(
                    "get_capabilities",
                    tenant_id,
                    principal_id,
                    True,
                    {"query": combined_text[:100], "response_type": "capabilities"},
                )
                task.artifacts = [
                    Artifact(
                        artifactId="capabilities_1", name="capabilities", parts=[Part(type="data", data=capabilities)]
                    )
                ]

            # Mark task as completed
            task.status = TaskStatus(state=TaskState.completed)

        except ServerError:
            # Re-raise ServerError as-is (will be caught by JSON-RPC handler)
            raise
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            # Try to get context for error logging
            try:
                auth_token = self._get_auth_token()
                if auth_token:
                    tool_context = self._create_tool_context_from_a2a(auth_token, "error_handler")
                    tenant_id = tool_context.tenant_id
                    principal_id = tool_context.principal_id
                else:
                    tenant_id = "unknown"
                    principal_id = "unknown"
            except:
                tenant_id = "unknown"
                principal_id = "unknown"

            self._log_a2a_operation(
                "message_processing",
                tenant_id,
                principal_id,
                False,
                {"error_type": type(e).__name__},
                str(e),
            )
            # Raise ServerError instead of creating failed task
            raise ServerError(InternalError(message=f"Message processing failed: {str(e)}"))

        self.tasks[task_id] = task
        return task

    async def on_message_send_stream(
        self,
        params: MessageSendParams,
        context: ServerCallContext | None = None,
    ) -> AsyncGenerator[Event]:
        """Handle 'message/stream' method for streaming requests.

        Args:
            params: Parameters including the message and configuration
            context: Server call context

        Yields:
            Event objects from the agent's execution
        """
        # For now, implement non-streaming behavior
        # In production, this would yield events as they occur
        task = await self.on_message_send(params, context)

        # Yield a single event with the complete task
        yield Event(type="task_update", data=task.model_dump())

    async def on_get_task(
        self,
        params: TaskQueryParams,
        context: ServerCallContext | None = None,
    ) -> Task | None:
        """Handle 'tasks/get' method to retrieve task status.

        Args:
            params: Parameters specifying the task ID
            context: Server call context

        Returns:
            Task object if found, otherwise None
        """
        task_id = params.task_id
        return self.tasks.get(task_id)

    async def on_cancel_task(
        self,
        params: TaskIdParams,
        context: ServerCallContext | None = None,
    ) -> Task | None:
        """Handle 'tasks/cancel' method to cancel a task.

        Args:
            params: Parameters specifying the task ID
            context: Server call context

        Returns:
            Task object with canceled status, or None if not found
        """
        task_id = params.task_id
        task = self.tasks.get(task_id)
        if task:
            task.status = TaskStatus(state=TaskState.canceled)
            self.tasks[task_id] = task
        return task

    async def on_resubscribe_to_task(
        self,
        params: Any,
        context: ServerCallContext | None = None,
    ) -> Any:
        """Handle task resubscription requests."""
        # Not implemented for now
        from a2a.types import UnsupportedOperationError

        raise UnsupportedOperationError("Task resubscription not supported")

    async def on_get_task_push_notification_config(
        self,
        params: Any,
        context: ServerCallContext | None = None,
    ) -> Any:
        """Handle get push notification config requests.

        Retrieves the push notification configuration for a specific config ID.
        """
        from a2a.types import InvalidParamsError, NotFoundError

        from src.core.database.database_session import get_db_session
        from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

        try:
            # Get authentication token
            auth_token = self._get_auth_token()
            if not auth_token:
                raise ServerError(InvalidRequestError(message="Missing authentication token"))

            # Resolve tenant and principal from auth token
            tool_context = self._create_tool_context_from_a2a(auth_token, "get_push_notification_config")

            # Extract config_id from params
            config_id = params.get("id") if isinstance(params, dict) else getattr(params, "id", None)
            if not config_id:
                raise ServerError(InvalidParamsError(message="Missing required parameter: id"))

            # Query database for config
            with get_db_session() as db:
                config = (
                    db.query(DBPushNotificationConfig)
                    .filter_by(
                        id=config_id,
                        tenant_id=tool_context.tenant_id,
                        principal_id=tool_context.principal_id,
                        is_active=True,
                    )
                    .first()
                )

                if not config:
                    raise ServerError(NotFoundError(message=f"Push notification config not found: {config_id}"))

                # Return A2A PushNotificationConfig format
                return {
                    "id": config.id,
                    "url": config.url,
                    "authentication": (
                        {"type": config.authentication_type or "none", "token": config.authentication_token}
                        if config.authentication_type
                        else None
                    ),
                    "token": config.validation_token,
                }

        except ServerError:
            raise
        except Exception as e:
            logger.error(f"Error getting push notification config: {e}")
            raise ServerError(InternalError(message=f"Failed to get push notification config: {str(e)}"))

    async def on_set_task_push_notification_config(
        self,
        params: Any,
        context: ServerCallContext | None = None,
    ) -> Any:
        """Handle set push notification config requests.

        Creates or updates a push notification configuration for async operation callbacks.
        Buyers use this to register webhook URLs where they want to receive status updates.
        """
        import uuid
        from datetime import UTC, datetime

        from a2a.types import InvalidParamsError

        from src.core.database.database_session import get_db_session
        from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

        try:
            # Get authentication token
            auth_token = self._get_auth_token()
            if not auth_token:
                raise ServerError(InvalidRequestError(message="Missing authentication token"))

            # Resolve tenant and principal from auth token
            tool_context = self._create_tool_context_from_a2a(auth_token, "set_push_notification_config")

            # Extract parameters
            if isinstance(params, dict):
                url = params.get("url")
                authentication = params.get("authentication")
                config_id = params.get("id") or f"pnc_{uuid.uuid4().hex[:16]}"
                validation_token = params.get("token")
                session_id = params.get("session_id")
            else:
                url = getattr(params, "url", None)
                authentication = getattr(params, "authentication", None)
                config_id = getattr(params, "id", None) or f"pnc_{uuid.uuid4().hex[:16]}"
                validation_token = getattr(params, "token", None)
                session_id = getattr(params, "session_id", None)

            if not url:
                raise ServerError(InvalidParamsError(message="Missing required parameter: url"))

            # Extract authentication details
            auth_type = None
            auth_token_value = None
            if authentication:
                if isinstance(authentication, dict):
                    auth_type = authentication.get("type")
                    auth_token_value = authentication.get("token")
                else:
                    auth_type = getattr(authentication, "type", None)
                    auth_token_value = getattr(authentication, "token", None)

            # Create or update configuration
            with get_db_session() as db:
                # Check if config exists
                existing_config = (
                    db.query(DBPushNotificationConfig)
                    .filter_by(id=config_id, tenant_id=tool_context.tenant_id, principal_id=tool_context.principal_id)
                    .first()
                )

                if existing_config:
                    # Update existing config
                    existing_config.url = url
                    existing_config.authentication_type = auth_type
                    existing_config.authentication_token = auth_token_value
                    existing_config.validation_token = validation_token
                    existing_config.session_id = session_id
                    existing_config.updated_at = datetime.now(UTC)
                    existing_config.is_active = True
                else:
                    # Create new config
                    new_config = DBPushNotificationConfig(
                        id=config_id,
                        tenant_id=tool_context.tenant_id,
                        principal_id=tool_context.principal_id,
                        session_id=session_id,
                        url=url,
                        authentication_type=auth_type,
                        authentication_token=auth_token_value,
                        validation_token=validation_token,
                        is_active=True,
                    )
                    db.add(new_config)

                db.commit()

                logger.info(
                    f"Push notification config {'updated' if existing_config else 'created'}: {config_id} for tenant {tool_context.tenant_id}"
                )

                # Return A2A response
                return {
                    "id": config_id,
                    "url": url,
                    "authentication": {"type": auth_type or "none", "token": auth_token_value} if auth_type else None,
                    "token": validation_token,
                    "status": "active",
                }

        except ServerError:
            raise
        except Exception as e:
            logger.error(f"Error setting push notification config: {e}")
            raise ServerError(InternalError(message=f"Failed to set push notification config: {str(e)}"))

    async def on_list_task_push_notification_config(
        self,
        params: Any,
        context: ServerCallContext | None = None,
    ) -> Any:
        """Handle list push notification config requests.

        Returns all active push notification configurations for the authenticated principal.
        """
        from src.core.database.database_session import get_db_session
        from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

        try:
            # Get authentication token
            auth_token = self._get_auth_token()
            if not auth_token:
                raise ServerError(InvalidRequestError(message="Missing authentication token"))

            # Resolve tenant and principal from auth token
            tool_context = self._create_tool_context_from_a2a(auth_token, "list_push_notification_configs")

            # Query database for all active configs
            with get_db_session() as db:
                configs = (
                    db.query(DBPushNotificationConfig)
                    .filter_by(tenant_id=tool_context.tenant_id, principal_id=tool_context.principal_id, is_active=True)
                    .all()
                )

                # Convert to A2A format
                configs_list = []
                for config in configs:
                    configs_list.append(
                        {
                            "id": config.id,
                            "url": config.url,
                            "authentication": (
                                {"type": config.authentication_type or "none", "token": config.authentication_token}
                                if config.authentication_type
                                else None
                            ),
                            "token": config.validation_token,
                            "created_at": config.created_at.isoformat() if config.created_at else None,
                        }
                    )

                logger.info(f"Listed {len(configs_list)} push notification configs for tenant {tool_context.tenant_id}")

                return {"configs": configs_list, "total_count": len(configs_list)}

        except ServerError:
            raise
        except Exception as e:
            logger.error(f"Error listing push notification configs: {e}")
            raise ServerError(InternalError(message=f"Failed to list push notification configs: {str(e)}"))

    async def on_delete_task_push_notification_config(
        self,
        params: Any,
        context: ServerCallContext | None = None,
    ) -> Any:
        """Handle delete push notification config requests.

        Marks a push notification configuration as inactive (soft delete).
        """
        from datetime import UTC, datetime

        from a2a.types import InvalidParamsError, NotFoundError

        from src.core.database.database_session import get_db_session
        from src.core.database.models import PushNotificationConfig as DBPushNotificationConfig

        try:
            # Get authentication token
            auth_token = self._get_auth_token()
            if not auth_token:
                raise ServerError(InvalidRequestError(message="Missing authentication token"))

            # Resolve tenant and principal from auth token
            tool_context = self._create_tool_context_from_a2a(auth_token, "delete_push_notification_config")

            # Extract config_id from params
            config_id = params.get("id") if isinstance(params, dict) else getattr(params, "id", None)
            if not config_id:
                raise ServerError(InvalidParamsError(message="Missing required parameter: id"))

            # Query database and mark as inactive
            with get_db_session() as db:
                config = (
                    db.query(DBPushNotificationConfig)
                    .filter_by(id=config_id, tenant_id=tool_context.tenant_id, principal_id=tool_context.principal_id)
                    .first()
                )

                if not config:
                    raise ServerError(NotFoundError(message=f"Push notification config not found: {config_id}"))

                # Soft delete by marking as inactive
                config.is_active = False
                config.updated_at = datetime.now(UTC)
                db.commit()

                logger.info(f"Deleted push notification config: {config_id} for tenant {tool_context.tenant_id}")

                return {
                    "id": config_id,
                    "status": "deleted",
                    "message": "Push notification configuration deleted successfully",
                }

        except ServerError:
            raise
        except Exception as e:
            logger.error(f"Error deleting push notification config: {e}")
            raise ServerError(InternalError(message=f"Failed to delete push notification config: {str(e)}"))

    async def _handle_explicit_skill(self, skill_name: str, parameters: dict, auth_token: str) -> dict:
        """Handle explicit AdCP skill invocations.

        Maps skill names to appropriate handlers and validates parameters.

        Args:
            skill_name: The AdCP skill name (e.g., "get_products")
            parameters: Dictionary of skill-specific parameters
            auth_token: Bearer token for authentication

        Returns:
            Dictionary containing the skill result

        Raises:
            ValueError: For unknown skills or invalid parameters
        """
        logger.info(f"Handling explicit skill: {skill_name} with parameters: {list(parameters.keys())}")

        # Map skill names to handlers
        skill_handlers = {
            # Core AdCP Media Buy Skills
            "get_products": self._handle_get_products_skill,
            "create_media_buy": self._handle_create_media_buy_skill,
            # ✅ NEW: Missing AdCP Discovery Skills (CRITICAL for protocol compliance)
            "list_creative_formats": self._handle_list_creative_formats_skill,
            "list_authorized_properties": self._handle_list_authorized_properties_skill,
            # ✅ NEW: Missing Media Buy Management Skills (CRITICAL for campaign lifecycle)
            "update_media_buy": self._handle_update_media_buy_skill,
            "get_media_buy_delivery": self._handle_get_media_buy_delivery_skill,
            "update_performance_index": self._handle_update_performance_index_skill,
            # AdCP Spec Creative Management (centralized library approach)
            "sync_creatives": self._handle_sync_creatives_skill,
            "list_creatives": self._handle_list_creatives_skill,
            # Creative Management & Approval
            "approve_creative": self._handle_approve_creative_skill,
            "get_media_buy_status": self._handle_get_media_buy_status_skill,
            "optimize_media_buy": self._handle_optimize_media_buy_skill,
            # Core AdCP Signals Skills
            "get_signals": self._handle_get_signals_skill,
            "search_signals": self._handle_search_signals_skill,
            # Legacy skill names (for backward compatibility)
            "get_pricing": lambda params, token: self._get_pricing(),
            "get_targeting": lambda params, token: self._get_targeting(),
        }

        if skill_name not in skill_handlers:
            available_skills = list(skill_handlers.keys())
            raise ServerError(
                MethodNotFoundError(message=f"Unknown skill '{skill_name}'. Available skills: {available_skills}")
            )

        try:
            handler = skill_handlers[skill_name]
            if skill_name in ["get_pricing", "get_targeting"]:
                # These are simple handlers without async
                return handler(parameters, auth_token)
            else:
                # These are async handlers that call core tools
                return await handler(parameters, auth_token)
        except ServerError:
            # Re-raise ServerError as-is (already properly formatted)
            raise
        except Exception as e:
            logger.error(f"Error in skill handler {skill_name}: {e}")
            raise ServerError(InternalError(message=f"Skill {skill_name} failed: {str(e)}"))

    async def _handle_get_products_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit get_products skill invocation."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="get_products",
            )

            # Map A2A parameters to GetProductsRequest
            brief = parameters.get("brief", "")
            promoted_offering = parameters.get("promoted_offering", "")

            if not brief and not promoted_offering:
                raise ServerError(
                    InvalidParamsError(message="Either 'brief' or 'promoted_offering' parameter is required")
                )

            # Use brief as promoted_offering if not provided
            if not promoted_offering and brief:
                promoted_offering = f"Business seeking to advertise: {brief}"

            # Call core function directly with individual parameters, not request object
            response = await core_get_products_tool(
                brief=brief, promoted_offering=promoted_offering, context=tool_context
            )

            # Handle both dict and object responses (defensive pattern)
            if isinstance(response, dict):
                products = response.get("products", [])
                message = response.get("message", "Products retrieved successfully")
                products_list = products
            else:
                products = response.products
                message = response.message or "Products retrieved successfully"
                products_list = [product.model_dump() for product in products]

            # Convert to A2A response format
            return {
                "products": products_list,
                "message": message,
            }

        except Exception as e:
            logger.error(f"Error in get_products skill: {e}")
            raise ServerError(InternalError(message=f"Unable to retrieve products: {str(e)}"))

    async def _handle_create_media_buy_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit create_media_buy skill invocation."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="create_media_buy",
            )

            # Validate required parameter: promoted_offering
            if "promoted_offering" not in parameters:
                return {
                    "success": False,
                    "message": "Missing required parameter: 'promoted_offering'",
                    "required_parameters": ["promoted_offering"],
                    "received_parameters": list(parameters.keys()),
                }

            # Check if using AdCP spec format (packages, budget, start_time, end_time)
            # or legacy format (product_ids, total_budget, flight_start_date, flight_end_date)
            has_packages = "packages" in parameters
            has_budget_obj = "budget" in parameters
            has_start_time = "start_time" in parameters
            has_end_time = "end_time" in parameters

            has_product_ids = "product_ids" in parameters
            has_total_budget = "total_budget" in parameters
            has_flight_start = "flight_start_date" in parameters
            has_flight_end = "flight_end_date" in parameters

            # Determine which format is being used
            if has_packages and has_budget_obj:
                # AdCP spec format - pass through to core function
                response = core_create_media_buy_tool(
                    promoted_offering=parameters["promoted_offering"],
                    po_number=parameters.get("po_number", f"A2A-{uuid.uuid4().hex[:8]}"),
                    buyer_ref=parameters.get("buyer_ref", f"A2A-{tool_context.principal_id}"),
                    packages=parameters["packages"],
                    start_time=parameters.get("start_time"),
                    end_time=parameters.get("end_time"),
                    budget=parameters.get("budget"),
                    targeting_overlay=parameters.get("custom_targeting", {}),
                    context=tool_context,
                )
            elif has_product_ids and has_total_budget:
                # Legacy flat format - convert to spec format
                response = core_create_media_buy_tool(
                    promoted_offering=parameters["promoted_offering"],
                    po_number=parameters.get("po_number", f"A2A-{uuid.uuid4().hex[:8]}"),
                    product_ids=parameters["product_ids"],
                    total_budget=float(parameters["total_budget"]),
                    start_date=parameters.get("flight_start_date"),
                    end_date=parameters.get("flight_end_date"),
                    targeting_overlay=parameters.get("custom_targeting", {}),
                    buyer_ref=parameters.get("buyer_ref", f"A2A-{tool_context.principal_id}"),
                    context=tool_context,
                )
            else:
                # Missing required parameters for both formats
                return {
                    "success": False,
                    "message": "Invalid parameters. Must provide either AdCP spec format (packages, budget, start_time, end_time) or legacy format (product_ids, total_budget, flight_start_date, flight_end_date)",
                    "required_parameters_spec": ["promoted_offering", "packages", "budget", "start_time", "end_time"],
                    "required_parameters_legacy": [
                        "promoted_offering",
                        "product_ids",
                        "total_budget",
                        "flight_start_date",
                        "flight_end_date",
                    ],
                    "received_parameters": list(parameters.keys()),
                }

            # Convert response to A2A format
            # Note: response.packages is already list[dict] per CreateMediaBuyResponse schema
            # See src/core/schemas.py:2034 - packages field is list[dict[str, Any]]
            return {
                "success": True,
                "media_buy_id": response.media_buy_id,
                "status": response.status,
                "message": response.message or "Media buy created successfully",
                "packages": response.packages if response.packages else [],  # Already list of dicts
                "next_steps": response.next_steps if hasattr(response, "next_steps") else [],
            }

        except Exception as e:
            logger.error(f"Error in create_media_buy skill: {e}")
            raise ServerError(InternalError(message=f"Failed to create media buy: {str(e)}"))

    async def _handle_sync_creatives_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit sync_creatives skill invocation (AdCP spec endpoint)."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="sync_creatives",
            )

            # Map A2A parameters - creatives is required
            if "creatives" not in parameters:
                return {
                    "success": False,
                    "message": "Missing required parameter: 'creatives'",
                    "required_parameters": ["creatives"],
                    "received_parameters": list(parameters.keys()),
                }

            # Call core function with individual parameters (fixing original validation bug)
            response = core_sync_creatives_tool(
                creatives=parameters["creatives"],
                media_buy_id=parameters.get("media_buy_id"),
                buyer_ref=parameters.get("buyer_ref"),
                assign_to_packages=parameters.get("assign_to_packages", []),
                upsert=parameters.get("upsert", True),
                context=tool_context,
            )

            # Convert response to A2A format
            return {
                "success": True,
                "synced_creatives": [creative.model_dump() for creative in response.synced_creatives],
                "failed_creatives": response.failed_creatives,
                "assignments": [assignment.model_dump() for assignment in response.assignments],
                "message": response.message or "Creatives synced successfully",
            }

        except Exception as e:
            logger.error(f"Error in sync_creatives skill: {e}")
            raise ServerError(InternalError(message=f"Failed to sync creatives: {str(e)}"))

    async def _handle_list_creatives_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit list_creatives skill invocation (AdCP spec endpoint)."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="list_creatives",
            )

            # Call core function with optional parameters (fixing original validation bug)
            response = core_list_creatives_tool(
                media_buy_id=parameters.get("media_buy_id"),
                buyer_ref=parameters.get("buyer_ref"),
                status=parameters.get("status"),
                format=parameters.get("format"),
                tags=parameters.get("tags", []),
                created_after=parameters.get("created_after"),
                created_before=parameters.get("created_before"),
                search=parameters.get("search"),
                page=parameters.get("page", 1),
                limit=parameters.get("limit", 50),
                sort_by=parameters.get("sort_by", "created_date"),
                sort_order=parameters.get("sort_order", "desc"),
                context=tool_context,
            )

            # Handle both dict and object responses (defensive pattern)
            if isinstance(response, dict):
                creatives_list = response.get("creatives", [])
                total_count = response.get("total_count", 0)
                page = response.get("page", 1)
                limit = response.get("limit", 50)
                has_more = response.get("has_more", False)
                message = response.get("message", "Creatives retrieved successfully")
            else:
                creatives_list = [creative.model_dump() for creative in response.creatives]
                total_count = response.total_count
                page = response.page
                limit = response.limit
                has_more = response.has_more
                message = response.message or "Creatives retrieved successfully"

            # Convert response to A2A format
            return {
                "success": True,
                "creatives": creatives_list,
                "total_count": total_count,
                "page": page,
                "limit": limit,
                "has_more": has_more,
                "message": message,
            }

        except Exception as e:
            logger.error(f"Error in list_creatives skill: {e}")
            raise ServerError(InternalError(message=f"Failed to list creatives: {str(e)}"))

    async def _handle_add_creative_assets_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit add_creative_assets skill invocation."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="add_creative_assets",
            )

            # Map A2A parameters to AddCreativeAssetsRequest
            # Required parameters
            if "media_buy_id" not in parameters and "buyer_ref" not in parameters:
                return {
                    "success": False,
                    "message": "Either 'media_buy_id' or 'buyer_ref' parameter is required",
                    "required_parameters": ["media_buy_id OR buyer_ref", "assets"],
                    "received_parameters": list(parameters.keys()),
                }

            if "assets" not in parameters:
                return {
                    "success": False,
                    "message": "Missing required parameter: 'assets'",
                    "required_parameters": ["media_buy_id OR buyer_ref", "assets"],
                    "received_parameters": list(parameters.keys()),
                }

            # Create request object with parameter mapping
            request = AddCreativeAssetsRequest(
                media_buy_id=parameters.get("media_buy_id"),
                buyer_ref=parameters.get("buyer_ref"),
                assets=parameters["assets"],
                creative_group_name=parameters.get("creative_group_name"),
            )

            # Call core function directly with individual parameters
            response = core_add_creative_assets_tool(
                assets=request.assets,
                media_buy_id=request.media_buy_id,
                buyer_ref=request.buyer_ref,
                context=tool_context,
            )

            # Convert response to A2A format
            return {
                "success": True,
                "message": response.message or "Creative assets added successfully",
                "creative_ids": response.creative_ids if hasattr(response, "creative_ids") else [],
                "status": response.status if hasattr(response, "status") else "pending_review",
            }

        except Exception as e:
            logger.error(f"Error in add_creative_assets skill: {e}")
            raise ServerError(InternalError(message=f"Failed to add creative assets: {str(e)}"))

    async def _handle_create_creative_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit create_creative skill invocation."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="create_creative",
            )

            # Map A2A parameters - format_id, content_uri, and name are required
            required_params = ["format_id", "content_uri", "name"]
            missing_params = [param for param in required_params if param not in parameters]

            if missing_params:
                return {
                    "success": False,
                    "message": f"Missing required parameters: {missing_params}",
                    "required_parameters": required_params,
                    "received_parameters": list(parameters.keys()),
                }

            # Call core function with individual parameters
            response = core_create_creative_tool(
                format_id=parameters["format_id"],
                content_uri=parameters["content_uri"],
                name=parameters["name"],
                group_id=parameters.get("group_id"),
                click_through_url=parameters.get("click_through_url"),
                metadata=parameters.get("metadata", {}),
                context=tool_context,
            )

            # Convert response to A2A format
            return {
                "success": True,
                "creative_id": response.creative_id,
                "message": response.message or "Creative created successfully in library",
                "status": response.status if hasattr(response, "status") else "created",
            }

        except Exception as e:
            logger.error(f"Error in create_creative skill: {e}")
            raise ServerError(InternalError(message=f"Failed to create creative: {str(e)}"))

    async def _handle_get_creatives_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit get_creatives skill invocation."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="get_creatives",
            )

            # Call core function with optional parameters
            response = core_get_creatives_tool(
                group_id=parameters.get("group_id"),
                media_buy_id=parameters.get("media_buy_id"),
                status=parameters.get("status"),
                tags=parameters.get("tags", []),
                include_assignments=parameters.get("include_assignments", False),
                context=tool_context,
            )

            # Convert response to A2A format
            return {
                "success": True,
                "creatives": [creative.model_dump() for creative in response.creatives],
                "message": response.message or f"Found {len(response.creatives)} creatives",
                "total_count": len(response.creatives),
            }

        except Exception as e:
            logger.error(f"Error in get_creatives skill: {e}")
            raise ServerError(InternalError(message=f"Failed to get creatives: {str(e)}"))

    async def _handle_assign_creative_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit assign_creative skill invocation."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="assign_creative",
            )

            # Map A2A parameters - media_buy_id, package_id, and creative_id are required
            required_params = ["media_buy_id", "package_id", "creative_id"]
            missing_params = [param for param in required_params if param not in parameters]

            if missing_params:
                return {
                    "success": False,
                    "message": f"Missing required parameters: {missing_params}",
                    "required_parameters": required_params,
                    "received_parameters": list(parameters.keys()),
                }

            # Call core function with individual parameters
            response = core_assign_creative_tool(
                media_buy_id=parameters["media_buy_id"],
                package_id=parameters["package_id"],
                creative_id=parameters["creative_id"],
                weight=parameters.get("weight", 100),
                percentage_goal=parameters.get("percentage_goal"),
                rotation_type=parameters.get("rotation_type", "weighted"),
                override_click_url=parameters.get("override_click_url"),
                context=tool_context,
            )

            # Convert response to A2A format
            return {
                "success": True,
                "assignment_id": response.assignment_id,
                "message": response.message or "Creative assigned successfully to media buy package",
                "status": response.status if hasattr(response, "status") else "assigned",
            }

        except Exception as e:
            logger.error(f"Error in assign_creative skill: {e}")
            raise ServerError(InternalError(message=f"Failed to assign creative: {str(e)}"))

    async def _handle_approve_creative_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit approve_creative skill invocation."""
        # TODO: Implement full approve_creative skill handler
        return {
            "success": False,
            "message": "approve_creative skill not yet implemented in explicit invocation",
            "parameters_received": parameters,
        }

    async def _handle_get_signals_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit get_signals skill invocation."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="get_signals",
            )

            # Map A2A parameters to GetSignalsRequest (per AdCP spec: signal_spec and deliver_to required)
            if "signal_spec" not in parameters or "deliver_to" not in parameters:
                return {
                    "success": False,
                    "message": "Missing required parameters: 'signal_spec' and 'deliver_to'",
                    "required_parameters": ["signal_spec", "deliver_to"],
                    "received_parameters": list(parameters.keys()),
                }

            request = GetSignalsRequest(
                signal_spec=parameters["signal_spec"],
                deliver_to=parameters["deliver_to"],
                filters=parameters.get("filters"),
                max_results=parameters.get("max_results"),
            )

            # Call core function directly
            response = await core_get_signals_tool(request, tool_context)

            # Handle both dict and object responses (defensive pattern)
            if isinstance(response, dict):
                signals = response.get("signals", [])
                signals_list = signals
            else:
                signals = response.signals
                signals_list = [signal.model_dump() for signal in signals]

            # Convert response to A2A format
            return {
                "signals": signals_list,
                "message": "Signals retrieved successfully",
                "total_count": len(signals_list),
            }

        except Exception as e:
            logger.error(f"Error in get_signals skill: {e}")
            raise ServerError(InternalError(message=f"Unable to retrieve signals: {str(e)}"))

    async def _handle_search_signals_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit search_signals skill invocation."""
        # TODO: Implement full search_signals skill handler
        return {
            "signals": [],
            "message": "search_signals skill not yet implemented in explicit invocation",
            "parameters_received": parameters,
        }

    async def _handle_get_media_buy_status_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit get_media_buy_status skill invocation."""
        # TODO: Implement full get_media_buy_status skill handler
        return {
            "success": False,
            "message": "get_media_buy_status skill not yet implemented in explicit invocation",
            "parameters_received": parameters,
        }

    async def _handle_optimize_media_buy_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit optimize_media_buy skill invocation."""
        # TODO: Implement full optimize_media_buy skill handler
        return {
            "success": False,
            "message": "optimize_media_buy skill not yet implemented in explicit invocation",
            "parameters_received": parameters,
        }

    async def _handle_list_creative_formats_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit list_creative_formats skill invocation (CRITICAL AdCP endpoint)."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="list_creative_formats",
            )

            # Build request from parameters (all optional)
            from src.core.schemas import ListCreativeFormatsRequest

            req = ListCreativeFormatsRequest(
                adcp_version=parameters.get("adcp_version", "1.0.0"),
                type=parameters.get("type"),
                standard_only=parameters.get("standard_only"),
                category=parameters.get("category"),
                format_ids=parameters.get("format_ids"),
            )

            # Call core function with request
            response = core_list_creative_formats_tool(req=req, context=tool_context)

            # Handle both dict and object responses (core function may return either based on INCLUDE_SCHEMAS_IN_RESPONSES)
            if isinstance(response, dict):
                # Response is already a dict (schema enhancement enabled)
                formats = response.get("formats", [])
                message = response.get("message", "Creative formats retrieved successfully")
                # Formats in dict are already serialized
                formats_list = formats
            else:
                # Response is ListCreativeFormatsResponse object
                formats = response.formats
                message = response.message or "Creative formats retrieved successfully"
                # Serialize Format objects to dicts
                formats_list = [format_obj.model_dump() for format_obj in formats]

            # Convert response to A2A format with schema validation
            from src.core.schema_validation import INCLUDE_SCHEMAS_IN_RESPONSES, enhance_a2a_response_with_schema

            a2a_response = {
                "success": True,
                "formats": formats_list,
                "message": message,
                "total_count": len(formats_list),
                "specification_version": "AdCP v2.4",
            }

            # Add schema validation metadata for client validation
            if INCLUDE_SCHEMAS_IN_RESPONSES:
                from src.core.schemas import ListCreativeFormatsResponse

                enhanced_response = enhance_a2a_response_with_schema(
                    response_data=a2a_response,
                    model_class=ListCreativeFormatsResponse,
                    include_full_schema=False,  # Set to True for development debugging
                )
                return enhanced_response

            return a2a_response

        except Exception as e:
            logger.error(f"Error in list_creative_formats skill: {e}")
            raise ServerError(InternalError(message=f"Unable to retrieve creative formats: {str(e)}"))

    async def _handle_list_authorized_properties_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit list_authorized_properties skill invocation (CRITICAL AdCP endpoint)."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="list_authorized_properties",
            )

            # Map A2A parameters to ListAuthorizedPropertiesRequest
            request = ListAuthorizedPropertiesRequest(tags=parameters.get("tags", []))

            # Call core function directly
            response = core_list_authorized_properties_tool(req=request, context=tool_context)

            # Handle both dict and object responses (defensive pattern)
            if isinstance(response, dict):
                properties = response.get("properties", [])
                tags = response.get("tags", {})
                properties_list = properties
            else:
                properties = response.properties
                tags = response.tags
                properties_list = [prop.model_dump() for prop in properties]

            # Convert response to A2A format
            return {
                "success": True,
                "properties": properties_list,
                "tags": tags,
                "message": "Authorized properties retrieved successfully",
                "total_count": len(properties_list),
            }

        except Exception as e:
            logger.error(f"Error in list_authorized_properties skill: {e}")
            raise ServerError(InternalError(message=f"Unable to retrieve authorized properties: {str(e)}"))

    async def _handle_update_media_buy_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit update_media_buy skill invocation (CRITICAL for campaign management)."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="update_media_buy",
            )

            # Validate required parameters
            if "media_buy_id" not in parameters:
                return {
                    "success": False,
                    "message": "Missing required parameter: 'media_buy_id'",
                    "required_parameters": ["media_buy_id", "updates"],
                    "received_parameters": list(parameters.keys()),
                }

            if "updates" not in parameters:
                return {
                    "success": False,
                    "message": "Missing required parameter: 'updates'",
                    "required_parameters": ["media_buy_id", "updates"],
                    "received_parameters": list(parameters.keys()),
                }

            # Call core function directly
            response = core_update_media_buy_tool(
                media_buy_id=parameters["media_buy_id"],
                updates=parameters["updates"],
                context=tool_context,
            )

            # Convert response to A2A format
            return response  # Raw function already returns dict format

        except Exception as e:
            logger.error(f"Error in update_media_buy skill: {e}")
            raise ServerError(InternalError(message=f"Unable to update media buy: {str(e)}"))

    async def _handle_get_media_buy_delivery_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit get_media_buy_delivery skill invocation (CRITICAL for monitoring).

        Accepts media_buy_ids (plural, per AdCP v1.6.0 spec) or media_buy_id (singular, legacy).
        """
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="get_media_buy_delivery",
            )

            # Extract media_buy_ids - support both plural (spec) and singular (legacy)
            media_buy_ids = parameters.get("media_buy_ids")
            if not media_buy_ids:
                # Fallback to singular form for backward compatibility
                media_buy_id = parameters.get("media_buy_id")
                if media_buy_id:
                    media_buy_ids = [media_buy_id]

            # Validate that we have at least one ID
            if not media_buy_ids:
                return {
                    "success": False,
                    "message": "Missing required parameter: 'media_buy_ids' (or 'media_buy_id' for single buy)",
                    "required_parameters": ["media_buy_ids"],
                    "received_parameters": list(parameters.keys()),
                }

            # Call core function directly with spec-compliant plural parameter
            response = core_get_media_buy_delivery_tool(
                media_buy_ids=media_buy_ids,
                context=tool_context,
            )

            # Convert response to dict for A2A format
            return response.model_dump() if hasattr(response, "model_dump") else response

        except Exception as e:
            logger.error(f"Error in get_media_buy_delivery skill: {e}")
            raise ServerError(InternalError(message=f"Unable to get media buy delivery: {str(e)}"))

    async def _handle_update_performance_index_skill(self, parameters: dict, auth_token: str) -> dict:
        """Handle explicit update_performance_index skill invocation (CRITICAL for optimization)."""
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="update_performance_index",
            )

            # Validate required parameters
            required_params = ["media_buy_id", "performance_data"]
            missing_params = [param for param in required_params if param not in parameters]

            if missing_params:
                return {
                    "success": False,
                    "message": f"Missing required parameters: {missing_params}",
                    "required_parameters": required_params,
                    "received_parameters": list(parameters.keys()),
                }

            # Call core function directly
            response = core_update_performance_index_tool(
                media_buy_id=parameters["media_buy_id"],
                performance_data=parameters["performance_data"],
                context=tool_context,
            )

            # Convert response to A2A format
            return response  # Raw function already returns dict format

        except Exception as e:
            logger.error(f"Error in update_performance_index skill: {e}")
            raise ServerError(InternalError(message=f"Unable to update performance index: {str(e)}"))

    async def _get_products(self, query: str, auth_token: str) -> dict:
        """Get available advertising products by calling core functions directly.

        Args:
            query: User's product query
            auth_token: Bearer token for authentication

        Returns:
            Dictionary containing product information
        """
        try:
            # Create ToolContext from A2A auth info
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="get_products",
            )

            # Extract promoted offering from the query or use a reasonable default
            promoted_offering = self._extract_promoted_offering_from_query(query)

            # Call core function directly using the underlying function
            response = await core_get_products_tool(
                brief=query, promoted_offering=promoted_offering, context=tool_context
            )

            # Convert to A2A response format
            return {
                "products": [product.model_dump() for product in response.products],
                "message": response.message or "Products retrieved successfully",
            }

        except Exception as e:
            logger.error(f"Error getting products: {e}")
            # Return empty products list instead of fallback data
            return {"products": [], "message": f"Unable to retrieve products: {str(e)}"}

    def _extract_promoted_offering_from_query(self, query: str) -> str:
        """Extract or infer promoted_offering from the user query.

        AdCP requires promoted_offering to be provided. We'll try to extract
        it from the query or provide a reasonable default.
        """
        # Look for common patterns that might indicate the promoted offering
        query_lower = query.lower()

        # If the query mentions specific brands or products, use those
        if "advertise" in query_lower or "promote" in query_lower:
            # Try to extract what they're promoting
            parts = query.split()
            for i, word in enumerate(parts):
                if word.lower() in ["advertise", "promote", "advertising", "promoting"]:
                    if i + 1 < len(parts):
                        # Take the next few words as the offering
                        offering_parts = parts[i + 1 : i + 4]  # Take up to 3 words
                        offering = " ".join(offering_parts).strip(".,!?")
                        if len(offering) > 5:  # Make sure it's substantial
                            return f"Business promoting {offering}"

        # Default offering based on query type
        if any(word in query_lower for word in ["video", "display", "banner", "ad"]):
            return "Brand advertising products and services"
        elif any(word in query_lower for word in ["coffee", "beverage", "food"]):
            return "Food and beverage company"
        elif any(word in query_lower for word in ["tech", "software", "app", "digital"]):
            return "Technology company digital products"
        else:
            # Generic fallback that should pass AdCP validation
            return "Business advertising products and services"

    def _get_pricing(self) -> dict:
        """Get pricing information.

        Returns:
            Dictionary containing pricing models and information
        """
        return {
            "pricing_models": [
                {
                    "type": "CPM",
                    "description": "Cost per thousand impressions",
                    "ranges": {
                        "video": {"min": 15, "max": 50},
                        "display": {"min": 2, "max": 10},
                        "native": {"min": 5, "max": 20},
                    },
                },
                {
                    "type": "CPC",
                    "description": "Cost per click",
                    "ranges": {"min": 0.50, "max": 5.00},
                },
                {
                    "type": "Guaranteed",
                    "description": "Fixed price for guaranteed delivery",
                    "minimum_commitment": 10000,
                },
            ],
            "volume_discounts": [
                {"threshold": 50000, "discount": "5%"},
                {"threshold": 100000, "discount": "10%"},
                {"threshold": 500000, "discount": "15%"},
            ],
        }

    def _get_targeting(self) -> dict:
        """Get available targeting options.

        Returns:
            Dictionary containing targeting capabilities
        """
        return {
            "targeting_options": {
                "demographics": {
                    "age_ranges": ["18-24", "25-34", "35-44", "45-54", "55+"],
                    "gender": ["male", "female", "unknown"],
                    "household_income": ["0-50k", "50-100k", "100-150k", "150k+"],
                },
                "geography": {
                    "levels": ["country", "state", "dma", "city", "zip"],
                    "available_countries": ["US", "CA", "UK", "AU"],
                },
                "interests": {
                    "categories": [
                        "Technology",
                        "Sports",
                        "Entertainment",
                        "Travel",
                        "Food & Dining",
                        "Health & Fitness",
                    ]
                },
                "contextual": {
                    "content_categories": ["News", "Sports", "Entertainment", "Business"],
                    "keywords": "Custom keyword targeting available",
                },
                "devices": {
                    "types": ["desktop", "mobile", "tablet", "ctv"],
                    "operating_systems": ["ios", "android", "windows", "macos"],
                },
            }
        }

    async def _create_media_buy(self, request: str, auth_token: str) -> dict:
        """Create a media buy based on the request.

        Args:
            request: User's media buy request
            auth_token: Bearer token for authentication

        Returns:
            Dictionary containing media buy creation result
        """
        # For now, return a mock response indicating authentication is working
        # but media buy creation needs more implementation
        try:
            # Verify authentication works
            tool_context = self._create_tool_context_from_a2a(
                auth_token=auth_token,
                tool_name="create_media_buy",
            )

            return {
                "success": False,
                "message": f"Authentication successful for {tool_context.principal_id}, but media buy creation needs more details",
                "required_fields": ["product_ids", "total_budget", "flight_start_date", "flight_end_date"],
                "authenticated_tenant": tool_context.tenant_id,
                "authenticated_principal": tool_context.principal_id,
                "example": {
                    "product_ids": ["video_premium"],
                    "total_budget": 10000,
                    "flight_start_date": "2025-02-01",
                    "flight_end_date": "2025-02-28",
                },
            }
        except Exception as e:
            logger.error(f"Error in media buy creation: {e}")
            raise ServerError(InternalError(message=f"Authentication failed: {str(e)}"))


def create_agent_card() -> AgentCard:
    """Create the agent card describing capabilities.

    Returns:
        AgentCard with AdCP Sales Agent capabilities
    """
    # Use new production domain for agent card
    # Note: This will be overridden dynamically in the endpoint handlers
    server_url = "https://sales-agent.scope3.com/a2a"

    from a2a.types import AgentCapabilities, AgentSkill

    # Create the agent card with minimal required fields
    agent_card = AgentCard(
        name="AdCP Sales Agent",
        description="AI agent for programmatic advertising campaigns via AdCP protocol",
        version="1.0.0",
        protocolVersion="1.0",
        capabilities=AgentCapabilities(),
        defaultInputModes=["message"],
        defaultOutputModes=["message"],
        skills=[
            # Core AdCP Media Buy Skills
            AgentSkill(
                id="get_products",
                name="get_products",
                description="Browse available advertising products and inventory",
                tags=["products", "inventory", "catalog", "adcp"],
            ),
            AgentSkill(
                id="create_media_buy",
                name="create_media_buy",
                description="Create advertising campaigns with products, targeting, and budget",
                tags=["campaign", "media", "buy", "adcp"],
            ),
            # ✅ NEW: Critical AdCP Discovery Endpoints (REQUIRED for protocol compliance)
            AgentSkill(
                id="list_creative_formats",
                name="list_creative_formats",
                description="List all available creative formats and specifications",
                tags=["creative", "formats", "specs", "discovery", "adcp"],
            ),
            AgentSkill(
                id="list_authorized_properties",
                name="list_authorized_properties",
                description="List authorized properties this agent can sell advertising for",
                tags=["properties", "authorization", "publisher", "adcp"],
            ),
            # ✅ NEW: Media Buy Management Skills (CRITICAL for campaign lifecycle)
            AgentSkill(
                id="update_media_buy",
                name="update_media_buy",
                description="Update existing media buy configuration and settings",
                tags=["campaign", "update", "management", "adcp"],
            ),
            AgentSkill(
                id="get_media_buy_delivery",
                name="get_media_buy_delivery",
                description="Get delivery metrics and performance data for media buys",
                tags=["delivery", "metrics", "performance", "monitoring", "adcp"],
            ),
            AgentSkill(
                id="update_performance_index",
                name="update_performance_index",
                description="Update performance data and optimization metrics",
                tags=["performance", "optimization", "metrics", "adcp"],
            ),
            # AdCP Spec Creative Management (centralized library approach)
            AgentSkill(
                id="sync_creatives",
                name="sync_creatives",
                description="Upload and manage creative assets to centralized library (AdCP spec)",
                tags=["creative", "sync", "library", "adcp", "spec"],
            ),
            AgentSkill(
                id="list_creatives",
                name="list_creatives",
                description="Search and query creative library with advanced filtering (AdCP spec)",
                tags=["creative", "library", "search", "adcp", "spec"],
            ),
            # Creative Management & Approval
            AgentSkill(
                id="approve_creative",
                name="approve_creative",
                description="Review and approve/reject creative assets (admin only)",
                tags=["creative", "approval", "review", "adcp"],
            ),
            AgentSkill(
                id="get_media_buy_status",
                name="get_media_buy_status",
                description="Check status and performance of media buys",
                tags=["status", "performance", "tracking", "adcp"],
            ),
            AgentSkill(
                id="optimize_media_buy",
                name="optimize_media_buy",
                description="Optimize media buy performance and targeting",
                tags=["optimization", "performance", "targeting", "adcp"],
            ),
            # Core AdCP Signals Skills (2 total)
            AgentSkill(
                id="get_signals",
                name="get_signals",
                description="Discover available targeting signals (audiences, contextual, etc.)",
                tags=["signals", "targeting", "discovery", "adcp"],
            ),
            AgentSkill(
                id="search_signals",
                name="search_signals",
                description="Search and filter targeting signals by criteria",
                tags=["signals", "search", "targeting", "adcp"],
            ),
            # Legacy Skills (for backward compatibility)
            AgentSkill(
                id="get_pricing",
                name="get_pricing",
                description="Get pricing information and rate cards",
                tags=["pricing", "cost", "budget", "legacy"],
            ),
            AgentSkill(
                id="get_targeting",
                name="get_targeting",
                description="Explore available targeting options",
                tags=["targeting", "audience", "demographics", "legacy"],
            ),
        ],
        url=server_url,
        documentation_url="https://github.com/your-org/adcp-sales-agent",
    )

    return agent_card


def main():
    """Main entry point for the A2A server."""
    host = os.getenv("A2A_HOST", "0.0.0.0")
    port = int(os.getenv("A2A_PORT", "8091"))

    # Initialize components
    agent_card = create_agent_card()
    request_handler = AdCPRequestHandler()

    logger.info(f"Starting AdCP A2A Agent on {host}:{port}")
    logger.info("Using official a2a-sdk with A2AStarletteApplication")

    # Create Starlette application
    a2a_app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )

    # Build the Starlette app with standard A2A specification endpoints
    app = a2a_app.build(
        agent_card_url="/.well-known/agent-card.json",  # Primary A2A discovery endpoint
        rpc_url="/a2a",  # Standard JSON-RPC endpoint
        extended_agent_card_url="/agent.json",
    )

    # Override the agent card endpoints to support tenant-specific URLs
    def create_dynamic_agent_card(request) -> AgentCard:
        """Create agent card with tenant-specific URL from request headers."""
        # Debug logging
        logger.info(f"Agent card request headers: {dict(request.headers)}")

        # Determine protocol based on host (localhost = HTTP, others = HTTPS)
        def get_protocol(hostname: str) -> str:
            """Return HTTP for localhost, HTTPS for production domains."""
            return "http" if hostname.startswith("localhost") or hostname.startswith("127.0.0.1") else "https"

        # Check for Approximated routing first (takes priority)
        apx_incoming_host = request.headers.get("Apx-Incoming-Host")
        if apx_incoming_host:
            # Use the original host from Approximated - preserve the exact domain
            protocol = get_protocol(apx_incoming_host)
            server_url = f"{protocol}://{apx_incoming_host}/a2a"
            logger.info(f"Using Apx-Incoming-Host: {apx_incoming_host} -> {server_url}")
        else:
            # Fallback to Host header
            host = request.headers.get("Host", "")
            if host and host != "sales-agent.scope3.com":
                # For external domains or localhost, use appropriate protocol
                protocol = get_protocol(host)
                server_url = f"{protocol}://{host}/a2a"
                logger.info(f"Using Host header: {host} -> {server_url}")
            else:
                # Default fallback - production HTTPS
                server_url = "https://sales-agent.scope3.com/a2a"
                logger.info(f"Using default URL: {server_url}")

        # Create a copy of the static agent card with dynamic URL
        dynamic_card = agent_card.model_copy()
        dynamic_card.url = server_url
        return dynamic_card

    # Replace the library's agent card endpoints with our dynamic ones
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def dynamic_agent_discovery(request):
        """Override for /.well-known/agent.json with tenant-specific URL."""
        dynamic_card = create_dynamic_agent_card(request)
        return JSONResponse(dynamic_card.model_dump())

    async def dynamic_agent_card_endpoint(request):
        """Override for /agent.json with tenant-specific URL."""
        dynamic_card = create_dynamic_agent_card(request)
        return JSONResponse(dynamic_card.model_dump())

    # Find and replace the existing routes to ensure proper A2A specification compliance
    new_routes = []
    for route in app.routes:
        if hasattr(route, "path"):
            if route.path == "/.well-known/agent.json":
                # Replace with our dynamic endpoint (legacy compatibility)
                new_routes.append(Route("/.well-known/agent.json", dynamic_agent_discovery, methods=["GET"]))
                logger.info("Replaced /.well-known/agent.json with dynamic version")
            elif route.path == "/.well-known/agent-card.json":
                # Replace with our dynamic endpoint (primary A2A discovery)
                new_routes.append(Route("/.well-known/agent-card.json", dynamic_agent_discovery, methods=["GET"]))
                logger.info("Replaced /.well-known/agent-card.json with dynamic version")
            elif route.path == "/agent.json":
                # Replace with our dynamic endpoint
                new_routes.append(Route("/agent.json", dynamic_agent_card_endpoint, methods=["GET"]))
                logger.info("Replaced /agent.json with dynamic version")
            else:
                new_routes.append(route)
        else:
            new_routes.append(route)

    # Update the app's router with new routes
    app.router.routes = new_routes

    # Add debug endpoint for tenant detection
    from starlette.routing import Route

    from src.core.config_loader import get_tenant_by_virtual_host

    async def debug_tenant_endpoint(request):
        """Debug endpoint to check tenant detection from headers."""
        headers = dict(request.headers)

        # Check for Apx-Incoming-Host header
        apx_host = headers.get("apx-incoming-host") or headers.get("Apx-Incoming-Host")
        host_header = headers.get("host") or headers.get("Host")

        # Resolve tenant using same logic as auth
        tenant_id = None
        tenant_name = None
        detection_method = None

        # Try Apx-Incoming-Host first
        if apx_host:
            tenant = get_tenant_by_virtual_host(apx_host)
            if tenant:
                tenant_id = tenant.get("tenant_id")
                tenant_name = tenant.get("name")
                detection_method = "apx-incoming-host"

        # Try Host header subdomain
        if not tenant_id and host_header:
            subdomain = host_header.split(".")[0] if "." in host_header else None
            if subdomain and subdomain not in ["localhost", "adcp-sales-agent", "www", "sales-agent"]:
                tenant_id = subdomain
                detection_method = "host-subdomain"

        response_data = {
            "tenant_id": tenant_id,
            "tenant_name": tenant_name,
            "detection_method": detection_method,
            "apx_incoming_host": apx_host,
            "host": host_header,
            "service": "a2a",
        }

        # Add X-Tenant-Id header to response
        response = JSONResponse(response_data)
        if tenant_id:
            response.headers["X-Tenant-Id"] = tenant_id

        return response

    # Add debug route
    app.router.routes.append(Route("/debug/tenant", debug_tenant_endpoint, methods=["GET"]))

    # Add middleware for backward compatibility with numeric messageId
    @app.middleware("http")
    async def messageId_compatibility_middleware(request, call_next):
        """Middleware to handle both numeric and string messageId for backward compatibility."""
        import json

        # Only process JSON-RPC requests to /a2a
        if request.url.path == "/a2a" and request.method == "POST":
            # Read the body
            body = await request.body()
            try:
                data = json.loads(body)

                # Check if this is a JSON-RPC request with numeric messageId
                if isinstance(data, dict) and "params" in data:
                    params = data.get("params", {})
                    if "message" in params and isinstance(params["message"], dict):
                        message = params["message"]
                        # Convert numeric messageId to string if needed
                        if "messageId" in message and isinstance(message["messageId"], int | float):
                            logger.warning(
                                f"Converting numeric messageId {message['messageId']} to string for compatibility"
                            )
                            message["messageId"] = str(message["messageId"])
                            # Update the request body
                            body = json.dumps(data).encode()

                # Also handle the outer id field for JSON-RPC
                if "id" in data and isinstance(data["id"], int | float):
                    logger.warning(f"Converting numeric JSON-RPC id {data['id']} to string for compatibility")
                    data["id"] = str(data["id"])
                    body = json.dumps(data).encode()

            except (json.JSONDecodeError, KeyError):
                # Not JSON or doesn't have expected structure, pass through
                pass

            # Create new request with potentially modified body
            from starlette.requests import Request

            request = Request(request.scope, receive=lambda: {"type": "http.request", "body": body})

        response = await call_next(request)
        return response

    # Add authentication middleware for Bearer token extraction
    @app.middleware("http")
    async def auth_middleware(request, call_next):
        """Extract Bearer token and set authentication context for A2A requests."""
        # Only process A2A endpoint requests (handle both /a2a and /a2a/)
        if request.url.path in ["/a2a", "/a2a/"] and request.method == "POST":
            # Extract Bearer token from Authorization header (case-insensitive)
            auth_header = request.headers.get("authorization", "").strip()
            # Also try Authorization with capital A (case variations)
            if not auth_header:
                auth_header = request.headers.get("Authorization", "").strip()

            logger.info(
                f"Processing A2A request to {request.url.path} with auth header: {'Bearer...' if auth_header.startswith('Bearer ') else repr(auth_header[:20]) + '...' if auth_header else 'missing'}"
            )

            if auth_header.startswith("Bearer "):
                token = auth_header[7:]  # Remove "Bearer " prefix
                # Store token and headers in thread-local storage for handler access
                _request_context.auth_token = token
                _request_context.request_headers = dict(request.headers)
                logger.info(f"Extracted Bearer token for A2A request: {token[:10]}...")
            else:
                logger.warning(f"A2A request to {request.url.path} missing Bearer token in Authorization header")
                _request_context.auth_token = None
                _request_context.request_headers = dict(request.headers)

        response = await call_next(request)

        # Clean up thread-local storage
        if hasattr(_request_context, "auth_token"):
            delattr(_request_context, "auth_token")
        if hasattr(_request_context, "request_headers"):
            delattr(_request_context, "request_headers")

        return response

    # Run with uvicorn
    import uvicorn

    logger.info("Standard A2A endpoints: /.well-known/agent.json, /a2a, /agent.json")
    logger.info("JSON-RPC 2.0 support enabled at /a2a")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
