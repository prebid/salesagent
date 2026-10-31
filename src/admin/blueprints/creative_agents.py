"""Creative agents management blueprint for admin UI."""

import logging

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.admin.utils.url_policy import redirect_if_url_blocked
from src.core.database.database_session import get_db_session
from src.core.database.models import CreativeAgent, Tenant
from src.core.database.repositories.uow import TenantConfigUoW

logger = logging.getLogger(__name__)

# Create Blueprint
creative_agents_bp = Blueprint("creative_agents", __name__)


@creative_agents_bp.route("/")
@require_tenant_access()
def list_creative_agents(tenant_id):
    """List all creative agents for a tenant."""
    try:
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            # Get tenant-specific creative agents
            stmt = select(CreativeAgent).filter_by(tenant_id=tenant_id).order_by(CreativeAgent.priority)
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
                        "priority": agent.priority,
                        "auth_type": agent.auth_type,
                        "has_auth": bool(agent.auth_credentials),
                        "created_at": agent.created_at,
                    }
                )

            return render_template(
                "creative_agents.html",
                tenant=tenant,
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                custom_agents=agents_list,
                script_name=request.environ.get("SCRIPT_NAME", ""),
            )

    except Exception as e:
        logger.error(f"Error loading creative agents: {e}", exc_info=True)
        flash("Error loading creative agents", "error")
        return redirect(url_for("tenants.tenant_settings", tenant_id=tenant_id))


@creative_agents_bp.route("/default-agent", methods=["POST"])
@log_admin_action("update_default_creative_agent")
@require_tenant_access()
def update_default_creative_agent(tenant_id):
    """Turn this seller's federation of the AdCP standard creative agent on or off.

    Off, the seller's format catalog and its ``creative_agents`` referrals list only the
    agents configured on this page (``tenants.use_default_creative_agent``).
    """
    use_default = request.form.get("use_default_creative_agent") == "on"
    with TenantConfigUoW(tenant_id) as uow:
        assert uow.tenant_config is not None
        if not uow.tenant_config.update_tenant(use_default_creative_agent=use_default):
            flash("Tenant not found", "error")
            return redirect(url_for("core.index"))

    flash(
        "The AdCP standard creative agent is now listed"
        if use_default
        else "The AdCP standard creative agent is no longer listed",
        "success",
    )
    return redirect(url_for("creative_agents.list_creative_agents", tenant_id=tenant_id))


@creative_agents_bp.route("/add", methods=["GET", "POST"])
@log_admin_action("add_creative_agent")
@require_tenant_access()
def add_creative_agent(tenant_id):
    """Add a new creative agent."""
    if request.method == "GET":
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            return render_template(
                "creative_agent_form.html",
                tenant=tenant,
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                agent=None,
                script_name=request.environ.get("SCRIPT_NAME", ""),
            )

    # POST - Create new creative agent
    try:
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            agent_url = request.form.get("agent_url", "").strip()
            name = request.form.get("name", "").strip()
            enabled = request.form.get("enabled") == "on"
            priority = int(request.form.get("priority", "10"))
            auth_type = request.form.get("auth_type", "").strip() or None
            auth_credentials = request.form.get("auth_credentials", "").strip() or None

            # The agent URL is stored now and fetched later, so egress policy is
            # applied at ingest — see src.admin.utils.url_policy. Without it the
            # operator learns of a refusal at dial time instead of at the form.
            if blocked := redirect_if_url_blocked(
                agent_url,
                "Agent URL",
                url_for("creative_agents.add_creative_agent", tenant_id=tenant_id),
            ):
                return blocked

            if not agent_url:
                flash("Agent URL is required", "error")
                return redirect(url_for("creative_agents.add_creative_agent", tenant_id=tenant_id))

            if not name:
                flash("Agent name is required", "error")
                return redirect(url_for("creative_agents.add_creative_agent", tenant_id=tenant_id))

            # Create new agent
            agent = CreativeAgent(
                tenant_id=tenant_id,
                agent_url=agent_url,
                name=name,
                enabled=enabled,
                priority=priority,
                auth_type=auth_type,
                auth_credentials=auth_credentials,
            )
            session.add(agent)
            session.commit()

            flash(f"Creative agent '{name}' added successfully", "success")
            return redirect(url_for("creative_agents.list_creative_agents", tenant_id=tenant_id))

    except Exception as e:
        logger.error(f"Error adding creative agent: {e}", exc_info=True)
        flash("Error adding creative agent", "error")
        return redirect(url_for("creative_agents.add_creative_agent", tenant_id=tenant_id))


@creative_agents_bp.route("/<int:agent_id>/edit", methods=["GET", "POST"])
@log_admin_action("edit_creative_agent")
@require_tenant_access()
def edit_creative_agent(tenant_id, agent_id):
    """Edit an existing creative agent."""
    if request.method == "GET":
        with get_db_session() as session:
            tenant = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.index"))

            stmt = select(CreativeAgent).filter_by(id=agent_id, tenant_id=tenant_id)
            agent = session.scalars(stmt).first()
            if not agent:
                flash("Creative agent not found", "error")
                return redirect(url_for("creative_agents.list_creative_agents", tenant_id=tenant_id))

            agent_dict = {
                "id": agent.id,
                "agent_url": agent.agent_url,
                "name": agent.name,
                "enabled": agent.enabled,
                "priority": agent.priority,
                "auth_type": agent.auth_type,
                "auth_credentials": agent.auth_credentials,
            }

            return render_template(
                "creative_agent_form.html",
                tenant=tenant,
                tenant_id=tenant_id,
                tenant_name=tenant.name,
                agent=agent_dict,
                script_name=request.environ.get("SCRIPT_NAME", ""),
            )

    # POST - Update creative agent
    try:
        with get_db_session() as session:
            stmt = select(CreativeAgent).filter_by(id=agent_id, tenant_id=tenant_id)
            agent = session.scalars(stmt).first()
            if not agent:
                flash("Creative agent not found", "error")
                return redirect(url_for("creative_agents.list_creative_agents", tenant_id=tenant_id))

            new_agent_url = request.form.get("agent_url", "").strip()
            if blocked := redirect_if_url_blocked(
                new_agent_url,
                "Agent URL",
                url_for("creative_agents.edit_creative_agent", tenant_id=tenant_id, agent_id=agent_id),
            ):
                return blocked

            agent.agent_url = new_agent_url
            agent.name = request.form.get("name", "").strip()
            agent.enabled = request.form.get("enabled") == "on"
            agent.priority = int(request.form.get("priority", "10"))
            agent.auth_type = request.form.get("auth_type", "").strip() or None

            # Only update credentials if provided
            new_credentials = request.form.get("auth_credentials", "").strip()
            if new_credentials:
                agent.auth_credentials = new_credentials

            if not agent.agent_url:
                flash("Agent URL is required", "error")
                return redirect(url_for("creative_agents.edit_creative_agent", tenant_id=tenant_id, agent_id=agent_id))

            if not agent.name:
                flash("Agent name is required", "error")
                return redirect(url_for("creative_agents.edit_creative_agent", tenant_id=tenant_id, agent_id=agent_id))

            session.commit()

            flash(f"Creative agent '{agent.name}' updated successfully", "success")
            return redirect(url_for("creative_agents.list_creative_agents", tenant_id=tenant_id))

    except Exception as e:
        logger.error(f"Error updating creative agent: {e}", exc_info=True)
        flash("Error updating creative agent", "error")
        return redirect(url_for("creative_agents.edit_creative_agent", tenant_id=tenant_id, agent_id=agent_id))


@creative_agents_bp.route("/<int:agent_id>/delete", methods=["DELETE"])
@require_tenant_access()
def delete_creative_agent(tenant_id, agent_id):
    """Delete a creative agent."""
    try:
        with get_db_session() as session:
            stmt = select(CreativeAgent).filter_by(id=agent_id, tenant_id=tenant_id)
            agent = session.scalars(stmt).first()
            if not agent:
                return jsonify({"error": "Creative agent not found"}), 404

            agent_name = agent.name
            session.delete(agent)
            session.commit()

            return jsonify({"success": True, "message": f"Creative agent '{agent_name}' deleted successfully"})

    except Exception as e:
        logger.error(f"Error deleting creative agent: {e}", exc_info=True)
        return jsonify({"error": "Error deleting creative agent"}), 500


@creative_agents_bp.route("/<int:agent_id>/test", methods=["POST"])
@log_admin_action("test_creative_agent")
@require_tenant_access()
def test_creative_agent(tenant_id, agent_id):
    """Test connection to a creative agent."""
    try:
        with get_db_session() as session:
            stmt = select(CreativeAgent).filter_by(id=agent_id, tenant_id=tenant_id)
            agent = session.scalars(stmt).first()
            if not agent:
                return jsonify({"error": "Creative agent not found"}), 404

            import asyncio

            from src.core.creative_agent_registry import CreativeAgentRegistry

            # The registry owns everything between the stored row and the dial:
            # the config mapping, the operator provenance and both error
            # vocabularies. Rebuilding any of that here is what previously let the
            # probe drop auth_header and timeout and so dial differently from
            # production. The JSON keys below are templates/creative_agents.html's
            # contract and stay as they are; only what they are projected FROM
            # changed.
            result = asyncio.run(CreativeAgentRegistry().probe_agent(agent))

            if result.ok:
                return jsonify(
                    {
                        "success": True,
                        "message": result.message,
                        "format_count": result.count,
                        "sample_formats": list(result.samples),
                    }
                )
            # ProbeResult.message is a required field, not an AdCP response
            # attribute -- the hook regex cannot tell the two apart.
            return jsonify({"success": False, "error": result.message}), 400  # noqa: response-attribute

    except Exception as e:
        logger.error(f"Error testing creative agent: {e}", exc_info=True)
        return (
            jsonify(
                {
                    "success": False,
                    "error": f"Connection failed: {str(e)}",
                }
            ),
            500,
        )
