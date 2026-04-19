"""Signals agents management blueprint for admin UI."""

import logging

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import SignalsAgent, Tenant
from src.core.security.url_validator import check_url_ssrf

logger = logging.getLogger(__name__)

# Create Blueprint
signals_agents_bp = Blueprint("signals_agents", __name__)


@signals_agents_bp.route("/")
@require_tenant_access()
def list_signals_agents(tenant_id):
    """List all signals agents for a tenant."""
    try:
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Get tenant-specific signals agents
            stmt = select(SignalsAgent).filter_by(tenant_id=tenant_id).order_by(SignalsAgent.name)
            custom_agents = session.scalars(stmt).all()

            # Convert to dict for template
            agents_list = []
            for agent in custom_agents:
                agents_list.append(
                    {
                        "id": agent.id,
                        "agent_url": agent.agent_url,
                        "name": agent.name,
                        "enabled": agent.enabled,
                        "auth_type": agent.auth_type,
                        "has_auth": bool(agent.auth_credentials),
                        "forward_promoted_offering": agent.forward_promoted_offering,
                        "timeout": agent.timeout,
                        "created_at": agent.created_at,
                    }
                )

            return render_template(
                "signals_agents.html",
                tenant=tenant,
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                custom_agents=agents_list,
                script_name=request.environ.get("SCRIPT_NAME", ""),
            )

    except Exception as e:
        logger.error(f"Error loading signals agents: {e}", exc_info=True)
        flash("Error loading signals agents", "error")
        return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))


@signals_agents_bp.route("/add", methods=["GET", "POST"])
@log_admin_action("add_signals_agent")
@require_tenant_access()
def add_signals_agent(tenant_id):
    """Add a new signals agent."""
    if request.method == "GET":
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            return render_template(
                "signals_agent_form.html",
                tenant=tenant,
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                agent=None,
                script_name=request.environ.get("SCRIPT_NAME", ""),
            )

    # POST - Create new signals agent
    try:
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            agent_url = request.form.get("agent_url", "").strip()
            name = request.form.get("name", "").strip()
            enabled = request.form.get("enabled") == "on"
            auth_type = request.form.get("auth_type", "").strip() or None
            auth_header = request.form.get("auth_header", "").strip() or None
            auth_credentials = request.form.get("auth_credentials", "").strip() or None
            forward_promoted_offering = request.form.get("forward_promoted_offering") == "on"
            timeout = int(request.form.get("timeout", "30"))

            if not agent_url:
                flash("Agent URL is required", "error")
                return redirect(url_for("signals_agents.add_signals_agent", tenant_id=tenant_id))

            is_safe, ssrf_error = check_url_ssrf(agent_url)
            if not is_safe:
                logger.warning("[SECURITY] Signals agent add rejected unsafe URL %r: %s", agent_url, ssrf_error)
                flash(f"Agent URL is not allowed: {ssrf_error}", "error")
                return redirect(url_for("signals_agents.add_signals_agent", tenant_id=tenant_id))

            if not name:
                flash("Agent name is required", "error")
                return redirect(url_for("signals_agents.add_signals_agent", tenant_id=tenant_id))

            # Create new agent
            agent = SignalsAgent(
                tenant_id=tenant_id,
                agent_url=agent_url,
                name=name,
                enabled=enabled,
                auth_type=auth_type,
                auth_header=auth_header,
                auth_credentials=auth_credentials,
                forward_promoted_offering=forward_promoted_offering,
                timeout=timeout,
            )
            session.add(agent)
            session.commit()

            flash(f"Signals agent '{name}' added successfully", "success")
            return redirect(url_for("signals_agents.list_signals_agents", tenant_id=tenant_id))

    except Exception as e:
        logger.error(f"Error adding signals agent: {e}", exc_info=True)
        flash("Error adding signals agent", "error")
        return redirect(url_for("signals_agents.add_signals_agent", tenant_id=tenant_id))


@signals_agents_bp.route("/<int:agent_id>/edit", methods=["GET", "POST"])
@log_admin_action("edit_signals_agent")
@require_tenant_access()
def edit_signals_agent(tenant_id, agent_id):
    """Edit an existing signals agent."""
    if request.method == "GET":
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            stmt = select(SignalsAgent).filter_by(id=agent_id, tenant_id=tenant_id)
            agent = session.scalars(stmt).first()
            if not agent:
                flash("Signals agent not found", "error")
                return redirect(url_for("signals_agents.list_signals_agents", tenant_id=tenant_id))

            agent_dict = {
                "id": agent.id,
                "agent_url": agent.agent_url,
                "name": agent.name,
                "enabled": agent.enabled,
                "auth_type": agent.auth_type,
                "auth_header": agent.auth_header,
                "auth_credentials": agent.auth_credentials,
                "forward_promoted_offering": agent.forward_promoted_offering,
                "timeout": agent.timeout,
            }

            return render_template(
                "signals_agent_form.html",
                tenant=tenant,
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                agent=agent_dict,
                script_name=request.environ.get("SCRIPT_NAME", ""),
            )

    # POST - Update signals agent
    try:
        with get_db_session() as session:
            stmt = select(SignalsAgent).filter_by(id=agent_id, tenant_id=tenant_id)
            agent = session.scalars(stmt).first()
            if not agent:
                flash("Signals agent not found", "error")
                return redirect(url_for("signals_agents.list_signals_agents", tenant_id=tenant_id))

            # Assign new form values to ORM object (in-memory only — not yet committed).
            # The SSRF check below reads agent.agent_url which is now the *submitted* value.
            agent.agent_url = request.form.get("agent_url", "").strip()
            agent.name = request.form.get("name", "").strip()
            agent.enabled = request.form.get("enabled") == "on"
            agent.auth_type = request.form.get("auth_type", "").strip() or None
            agent.auth_header = request.form.get("auth_header", "").strip() or None
            agent.forward_promoted_offering = request.form.get("forward_promoted_offering") == "on"
            agent.timeout = int(request.form.get("timeout", "30"))

            # Only update credentials if provided
            new_credentials = request.form.get("auth_credentials", "").strip()
            if new_credentials:
                agent.auth_credentials = new_credentials

            if not agent.agent_url:
                flash("Agent URL is required", "error")
                return redirect(url_for("signals_agents.edit_signals_agent", tenant_id=tenant_id, agent_id=agent_id))

            # Validate the newly submitted URL — agent.agent_url is the form value set above.
            is_safe, ssrf_error = check_url_ssrf(agent.agent_url)
            if not is_safe:
                logger.warning("[SECURITY] Signals agent edit rejected unsafe URL %r: %s", agent.agent_url, ssrf_error)
                flash(f"Agent URL is not allowed: {ssrf_error}", "error")
                return redirect(url_for("signals_agents.edit_signals_agent", tenant_id=tenant_id, agent_id=agent_id))

            if not agent.name:
                flash("Agent name is required", "error")
                return redirect(url_for("signals_agents.edit_signals_agent", tenant_id=tenant_id, agent_id=agent_id))

            session.commit()

            flash(f"Signals agent '{agent.name}' updated successfully", "success")
            return redirect(url_for("signals_agents.list_signals_agents", tenant_id=tenant_id))

    except Exception as e:
        logger.error(f"Error updating signals agent: {e}", exc_info=True)
        flash("Error updating signals agent", "error")
        return redirect(url_for("signals_agents.edit_signals_agent", tenant_id=tenant_id, agent_id=agent_id))


@signals_agents_bp.route("/<int:agent_id>/delete", methods=["DELETE"])
@require_tenant_access()
def delete_signals_agent(tenant_id, agent_id):
    """Delete a signals agent."""
    try:
        with get_db_session() as session:
            stmt = select(SignalsAgent).filter_by(id=agent_id, tenant_id=tenant_id)
            agent = session.scalars(stmt).first()
            if not agent:
                return jsonify({"error": "Signals agent not found"}), 404

            agent_name = agent.name
            session.delete(agent)
            session.commit()

            return jsonify({"success": True, "message": f"Signals agent '{agent_name}' deleted successfully"})

    except Exception as e:
        logger.error(f"Error deleting signals agent: {e}", exc_info=True)
        return jsonify({"error": "Error deleting signals agent"}), 500


@signals_agents_bp.route("/<int:agent_id>/test", methods=["POST"])
@log_admin_action("test_signals_agent")
@require_tenant_access()
def test_signals_agent(tenant_id, agent_id):
    """Test connection to a signals agent."""
    try:
        with get_db_session() as session:
            stmt = select(SignalsAgent).filter_by(id=agent_id, tenant_id=tenant_id)
            agent = session.scalars(stmt).first()
            if not agent:
                return jsonify({"error": "Signals agent not found"}), 404

            # Test connection by fetching signals
            import asyncio

            from src.core.signals_agent_registry import SignalsAgentRegistry

            registry = SignalsAgentRegistry()

            # Build agent config
            auth = None
            if agent.auth_type and agent.auth_credentials:
                auth = {
                    "type": agent.auth_type,
                    "credentials": agent.auth_credentials,
                }

            # Validate URL before making outbound request (defence-in-depth: stored URLs
            # may pre-date the add/edit SSRF checks)
            is_safe, ssrf_error = check_url_ssrf(agent.agent_url)
            if not is_safe:
                logger.warning(
                    "[SECURITY] Signals agent test-connection rejected unsafe URL %r: %s",
                    agent.agent_url,
                    ssrf_error,
                )
                return jsonify({"success": False, "error": f"Agent URL is not allowed: {ssrf_error}"}), 400

            # Test connection
            # Use asyncio.run() instead of new_event_loop() for better compatibility with adcp library
            result = asyncio.run(registry.test_connection(agent.agent_url, auth=auth, auth_header=agent.auth_header))

            if result.get("success"):
                return jsonify(
                    {
                        "success": True,
                        "message": result.get("message", "Successfully connected"),
                        "signal_count": result.get("signal_count", 0),
                    }
                )
            else:
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": result.get("error", "Connection failed"),
                        }
                    ),
                    400,
                )

    except Exception as e:
        logger.error(f"Error testing signals agent: {e}", exc_info=True)
        return (
            jsonify(
                {
                    "success": False,
                    "error": f"Connection failed: {str(e)}",
                }
            ),
            500,
        )
