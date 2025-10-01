"""Admin blueprint for managing authorized properties."""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from src.admin.utils import require_tenant_access
from src.core.database.database_session import get_db_session
from src.core.database.models import AuthorizedProperty, PropertyTag, Tenant
from src.core.schemas import (
    PROPERTY_ERROR_MESSAGES,
    PROPERTY_REQUIRED_FIELDS,
    PROPERTY_TYPES,
    SUPPORTED_UPLOAD_FILE_TYPES,
)
from src.services.property_verification_service import get_property_verification_service

logger = logging.getLogger(__name__)

authorized_properties_bp = Blueprint("authorized_properties", __name__)


def _validate_property_form(request) -> tuple[bool, str, tuple[str, str, str, list[dict[str, str]], list[str]]]:
    """Validate property form data and return parsed values.

    Returns:
        Tuple of (is_valid, error_message, (property_type, name, publisher_domain, identifiers, tags))
    """
    # Get form data
    property_type = request.form.get("property_type", "").strip()
    name = request.form.get("name", "").strip()
    publisher_domain = request.form.get("publisher_domain", "").strip()

    # Validate required fields
    if not property_type or not name or not publisher_domain:
        return False, PROPERTY_ERROR_MESSAGES["missing_required_field"], None

    # Validate property_type
    if property_type not in PROPERTY_TYPES:
        return (
            False,
            PROPERTY_ERROR_MESSAGES["invalid_property_type"].format(
                property_type=property_type, valid_types=", ".join(PROPERTY_TYPES)
            ),
            None,
        )

    # Parse identifiers from form
    identifiers = []
    identifier_count = 0
    while True:
        identifier_type = request.form.get(f"identifier_type_{identifier_count}", "").strip()
        identifier_value = request.form.get(f"identifier_value_{identifier_count}", "").strip()

        if not identifier_type and not identifier_value:
            break

        if identifier_type and identifier_value:
            identifiers.append({"type": identifier_type, "value": identifier_value})
        elif identifier_type or identifier_value:
            return False, PROPERTY_ERROR_MESSAGES["identifier_incomplete"].format(index=identifier_count + 1), None

        identifier_count += 1

    if not identifiers:
        return False, PROPERTY_ERROR_MESSAGES["at_least_one_identifier"], None

    # Parse tags
    tags = []
    tag_values = request.form.getlist("tags")
    for tag in tag_values:
        if tag.strip():
            tags.append(tag.strip())

    return True, "", (property_type, name, publisher_domain, identifiers, tags)


def _parse_properties_file(file) -> tuple[list[dict[str, Any]], str]:
    """Parse properties from uploaded file.

    Returns:
        Tuple of (properties_data, error_message)
    """
    try:
        file_content = file.read().decode("utf-8")
        properties_data = []

        if file.filename.lower().endswith(".json"):
            try:
                data = json.loads(file_content)
                if isinstance(data, list):
                    properties_data = data
                elif isinstance(data, dict) and "properties" in data:
                    properties_data = data["properties"]
                else:
                    return [], "JSON file must contain an array of properties or an object with 'properties' key"
            except json.JSONDecodeError as e:
                return [], PROPERTY_ERROR_MESSAGES["invalid_json"].format(error=str(e))

        elif file.filename.lower().endswith(".csv"):
            # TODO: Implement CSV parsing
            return [], "CSV upload is not yet implemented"

        return properties_data, ""
    except Exception as e:
        return [], f"Error reading file: {str(e)}"


def _save_properties_batch(properties_data: list[dict[str, Any]], tenant_id: str) -> tuple[int, int, list[str]]:
    """Save a batch of properties to the database.

    Returns:
        Tuple of (success_count, error_count, errors)
    """
    success_count = 0
    error_count = 0
    errors = []

    with get_db_session() as db_session:
        for i, prop_data in enumerate(properties_data):
            try:
                # Validate required fields
                for field in PROPERTY_REQUIRED_FIELDS:
                    if field not in prop_data:
                        raise ValueError(f"Missing required field: {field}")

                # Validate property_type
                if prop_data["property_type"] not in PROPERTY_TYPES:
                    raise ValueError(f"Invalid property_type: {prop_data['property_type']}")

                # Validate identifiers
                if not isinstance(prop_data["identifiers"], list) or len(prop_data["identifiers"]) == 0:
                    raise ValueError("identifiers must be a non-empty array")

                for ident in prop_data["identifiers"]:
                    if not isinstance(ident, dict) or "type" not in ident or "value" not in ident:
                        raise ValueError("Each identifier must have 'type' and 'value' fields")

                # Generate property_id if not provided
                property_id = prop_data.get("property_id", str(uuid.uuid4()))

                # Check if property already exists
                existing_property = (
                    db_session.query(AuthorizedProperty)
                    .filter(
                        AuthorizedProperty.tenant_id == tenant_id,
                        AuthorizedProperty.property_id == property_id,
                    )
                    .first()
                )

                if existing_property:
                    # Update existing property
                    existing_property.property_type = prop_data["property_type"]
                    existing_property.name = prop_data["name"]
                    existing_property.identifiers = prop_data["identifiers"]
                    existing_property.tags = prop_data.get("tags", [])
                    existing_property.publisher_domain = prop_data["publisher_domain"]
                    existing_property.verification_status = "pending"
                    existing_property.verification_checked_at = None
                    existing_property.verification_error = None
                    existing_property.updated_at = datetime.utcnow()
                else:
                    # Create new property
                    new_property = AuthorizedProperty(
                        property_id=property_id,
                        tenant_id=tenant_id,
                        property_type=prop_data["property_type"],
                        name=prop_data["name"],
                        identifiers=prop_data["identifiers"],
                        tags=prop_data.get("tags", []),
                        publisher_domain=prop_data["publisher_domain"],
                        verification_status="pending",
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                    db_session.add(new_property)

                success_count += 1

            except Exception as e:
                error_count += 1
                errors.append(f"Property {i+1}: {str(e)}")
                logger.error(f"Error processing property {i+1}: {e}")

        # Commit all changes
        if success_count > 0:
            db_session.commit()

    return success_count, error_count, errors


def _parse_and_save_properties_file(file, tenant_id: str) -> tuple[int, int, list[str]]:
    """Parse properties file and save to database.

    Returns:
        Tuple of (success_count, error_count, errors)
    """
    properties_data, parse_error = _parse_properties_file(file)
    if parse_error:
        return 0, 1, [parse_error]

    return _save_properties_batch(properties_data, tenant_id)


def _construct_agent_url(tenant_id: str, request) -> str:
    """Construct the agent URL using existing tenant resolution logic."""
    import os

    from src.core.database.models import Tenant

    logger.info(f"🏗️ Constructing agent URL for tenant: {tenant_id}")

    # Check if we have an explicit override for testing
    override_url = os.environ.get("ADCP_AGENT_URL")
    if override_url:
        logger.info(f"🔧 Using ADCP_AGENT_URL override: {override_url}")
        return override_url

    # Get tenant information directly from database using tenant_id parameter
    try:
        with get_db_session() as db_session:
            tenant_obj = db_session.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
            if not tenant_obj:
                raise ValueError(f"Tenant {tenant_id} not found")

            subdomain = tenant_obj.subdomain or tenant_id
            virtual_host = tenant_obj.virtual_host

        logger.info(f"🏢 Tenant info - subdomain: '{subdomain}', virtual_host: '{virtual_host}'")

        # In production, use the existing virtual host system
        if os.environ.get("PRODUCTION") == "true":
            if virtual_host:
                url = f"https://{virtual_host}"
                logger.info(f"🌐 Production: using virtual_host -> {url}")
                return url
            else:
                # Fallback to subdomain pattern
                url = f"https://{subdomain}.sales-agent.scope3.com"
                logger.info(f"🌐 Production: using subdomain pattern -> {url}")
                return url

        # For development, use MCP server port
        mcp_port = os.environ.get("ADCP_SALES_PORT", "8080")
        url = f"http://localhost:{mcp_port}"
        logger.info(f"🛠️ Development: using localhost -> {url}")
        return url

    except Exception as e:
        # Fallback if tenant context unavailable
        logger.warning(f"⚠️ Failed to get tenant context: {e}")
        mcp_port = os.environ.get("ADCP_SALES_PORT", "8080")
        url = f"http://localhost:{mcp_port}"
        logger.info(f"🆘 Fallback: using localhost -> {url}")
        return url


@authorized_properties_bp.route("/<tenant_id>/authorized-properties")
@require_tenant_access()
def list_authorized_properties(tenant_id):
    """List all authorized properties for a tenant."""
    try:
        logger.info(f"Accessing authorized properties for tenant: {tenant_id}")
        logger.info(f"Session keys: {list(session.keys())}")
        logger.info(f"User in session: {session.get('user')}")

        with get_db_session() as db_session:
            # Get tenant info
            logger.info(f"Querying tenant: {tenant_id}")
            tenant = db_session.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
            if not tenant:
                logger.error(f"Tenant not found: {tenant_id}")
                flash(PROPERTY_ERROR_MESSAGES["tenant_not_found"], "error")
                return redirect(url_for("core.admin_dashboard"))

            logger.info(f"Found tenant: {tenant.name}")

            # Get all properties for this tenant
            logger.info("Querying authorized properties...")
            properties = (
                db_session.query(AuthorizedProperty)
                .filter(AuthorizedProperty.tenant_id == tenant_id)
                .order_by(AuthorizedProperty.created_at.desc())
                .all()
            )

            logger.info(f"Found {len(properties)} properties")

            # Get property counts by status
            property_counts = {
                "total": len(properties),
                "verified": len([p for p in properties if p.verification_status == "verified"]),
                "pending": len([p for p in properties if p.verification_status == "pending"]),
                "failed": len([p for p in properties if p.verification_status == "failed"]),
            }

            logger.info(f"Property counts: {property_counts}")
            logger.info("Rendering template...")

            # Get environment info for dev/production detection
            is_production = os.environ.get("PRODUCTION") == "true"

            return render_template(
                "authorized_properties_list.html",
                tenant=tenant,
                properties=properties,
                property_counts=property_counts,
                session=session,
                user=session.get("user"),
                is_production=is_production,
            )

    except Exception as e:
        logger.error(f"Error listing authorized properties: {e}", exc_info=True)
        flash(f"Error loading properties: {str(e)}", "error")
        return redirect(url_for("tenants.tenant_dashboard", tenant_id=tenant_id))


@authorized_properties_bp.route("/<tenant_id>/authorized-properties/upload", methods=["GET", "POST"])
@require_tenant_access()
def upload_authorized_properties(tenant_id):
    """Upload authorized properties from JSON or CSV file."""
    if request.method == "GET":
        try:
            with get_db_session() as db_session:
                tenant = db_session.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
                if not tenant:
                    flash(PROPERTY_ERROR_MESSAGES["tenant_not_found"], "error")
                    return redirect(url_for("core.admin_dashboard"))

                # Get existing tags for this tenant
                existing_tags = db_session.query(PropertyTag).filter(PropertyTag.tenant_id == tenant_id).all()

                return render_template(
                    "authorized_properties_upload.html",
                    tenant=tenant,
                    existing_tags=existing_tags,
                    session=session,
                    user=session.get("user"),
                )

        except Exception as e:
            logger.error(f"Error loading upload page: {e}")
            flash(f"Error loading upload page: {str(e)}", "error")
            return redirect(url_for("authorized_properties.list_authorized_properties", tenant_id=tenant_id))

    # Handle POST request (file upload)
    try:
        if "file" not in request.files:
            flash(PROPERTY_ERROR_MESSAGES["no_file_selected"], "error")
            return redirect(request.url)

        file = request.files["file"]
        if file.filename == "":
            flash(PROPERTY_ERROR_MESSAGES["no_file_selected"], "error")
            return redirect(request.url)

        if not file.filename.lower().endswith(tuple(SUPPORTED_UPLOAD_FILE_TYPES)):
            flash(PROPERTY_ERROR_MESSAGES["invalid_file_type"], "error")
            return redirect(request.url)

        # Parse and save properties
        success_count, error_count, errors = _parse_and_save_properties_file(file, tenant_id)

        # Show results
        if success_count > 0:
            flash(f"Successfully uploaded {success_count} properties", "success")

        if error_count > 0:
            flash(f"Failed to upload {error_count} properties. See errors below.", "warning")
            for error in errors[:10]:  # Show first 10 errors
                flash(error, "error")

        return redirect(url_for("authorized_properties.list_authorized_properties", tenant_id=tenant_id))

    except Exception as e:
        logger.error(f"Error uploading properties: {e}")
        flash(f"Error uploading properties: {str(e)}", "error")
        return redirect(url_for("authorized_properties.list_authorized_properties", tenant_id=tenant_id))


# Manual verification route removed for security - all verification must go through adagents.json checking


@authorized_properties_bp.route("/<tenant_id>/authorized-properties/<property_id>/delete", methods=["POST"])
@require_tenant_access()
def delete_property(tenant_id, property_id):
    """Delete an authorized property."""
    try:
        with get_db_session() as db_session:
            property_obj = (
                db_session.query(AuthorizedProperty)
                .filter(
                    AuthorizedProperty.tenant_id == tenant_id,
                    AuthorizedProperty.property_id == property_id,
                )
                .first()
            )

            if not property_obj:
                flash(PROPERTY_ERROR_MESSAGES["property_not_found"], "error")
                return redirect(url_for("authorized_properties.list_authorized_properties", tenant_id=tenant_id))

            property_name = property_obj.name
            db_session.delete(property_obj)
            db_session.commit()
            flash(f"Property '{property_name}' deleted successfully", "success")

    except Exception as e:
        logger.error(f"Error deleting property: {e}")
        flash(f"Error deleting property: {str(e)}", "error")

    return redirect(url_for("authorized_properties.list_authorized_properties", tenant_id=tenant_id))


@authorized_properties_bp.route("/<tenant_id>/property-tags")
@require_tenant_access()
def list_property_tags(tenant_id):
    """List and manage property tags for a tenant."""
    try:
        with get_db_session() as db_session:
            # Get tenant info
            tenant = db_session.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
            if not tenant:
                flash("Tenant not found", "error")
                return redirect(url_for("core.admin_dashboard"))

            # Get all tags for this tenant
            tags = (
                db_session.query(PropertyTag)
                .filter(PropertyTag.tenant_id == tenant_id)
                .order_by(PropertyTag.name)
                .all()
            )

            return render_template(
                "property_tags_list.html",
                tenant=tenant,
                tags=tags,
                session=session,
                user=session.get("user"),
            )

    except Exception as e:
        logger.error(f"Error listing property tags: {e}")
        flash(f"Error loading tags: {str(e)}", "error")
        return redirect(url_for("tenants.tenant_dashboard", tenant_id=tenant_id))


@authorized_properties_bp.route("/<tenant_id>/property-tags/create", methods=["POST"])
@require_tenant_access()
def create_property_tag(tenant_id):
    """Create a new property tag."""
    try:
        tag_id = request.form.get("tag_id", "").strip()
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()

        if not tag_id or not name or not description:
            flash(PROPERTY_ERROR_MESSAGES["all_fields_required"], "error")
            return redirect(url_for("authorized_properties.list_property_tags", tenant_id=tenant_id))

        # Validate tag_id format (lowercase, underscores only)
        if not tag_id.replace("_", "").replace("-", "").isalnum():
            flash(PROPERTY_ERROR_MESSAGES["invalid_tag_id"], "error")
            return redirect(url_for("authorized_properties.list_property_tags", tenant_id=tenant_id))

        tag_id = tag_id.lower().replace("-", "_")

        with get_db_session() as db_session:
            # Check if tag already exists
            existing_tag = (
                db_session.query(PropertyTag)
                .filter(PropertyTag.tenant_id == tenant_id, PropertyTag.tag_id == tag_id)
                .first()
            )

            if existing_tag:
                flash(PROPERTY_ERROR_MESSAGES["tag_already_exists"].format(tag_id=tag_id), "error")
                return redirect(url_for("authorized_properties.list_property_tags", tenant_id=tenant_id))

            # Create new tag
            new_tag = PropertyTag(
                tag_id=tag_id,
                tenant_id=tenant_id,
                name=name,
                description=description,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )

            db_session.add(new_tag)
            db_session.commit()
            flash(f"Tag '{name}' created successfully", "success")

    except Exception as e:
        logger.error(f"Error creating property tag: {e}")
        flash(f"Error creating tag: {str(e)}", "error")

    return redirect(url_for("authorized_properties.list_property_tags", tenant_id=tenant_id))


@authorized_properties_bp.route("/<tenant_id>/authorized-properties/verify-all", methods=["POST"])
@require_tenant_access()
def verify_all_properties(tenant_id):
    """Verify all pending properties against their adagents.json files."""
    try:
        # In production, always construct agent URL from tenant context
        # Dev overrides only allowed in development
        is_production = os.environ.get("PRODUCTION") == "true"

        if is_production:
            # Production: ignore any dev overrides, always use tenant context
            agent_url = _construct_agent_url(tenant_id, request)
        else:
            # Development: allow dev overrides from form
            agent_url = request.form.get("dev_agent_url", "").strip() or request.form.get("agent_url", "").strip()
            if not agent_url:
                agent_url = _construct_agent_url(tenant_id, request)

        verification_service = get_property_verification_service()
        results = verification_service.verify_all_properties(tenant_id, agent_url)

        # Display results
        if results["verified"] > 0:
            flash(f"Successfully verified {results['verified']} properties", "success")

        if results["failed"] > 0:
            flash(f"Failed to verify {results['failed']} properties", "warning")

            # Show first few errors
            for error in results["errors"][:5]:
                flash(error, "error")

            if len(results["errors"]) > 5:
                flash(f"... and {len(results['errors']) - 5} more errors", "error")

        if results["total_checked"] == 0:
            flash("No pending properties to verify", "info")

    except Exception as e:
        logger.error(f"Error in bulk verification: {e}")
        flash(f"Error verifying properties: {str(e)}", "error")

    return redirect(url_for("authorized_properties.list_authorized_properties", tenant_id=tenant_id))


@authorized_properties_bp.route("/<tenant_id>/authorized-properties/<property_id>/verify-auto", methods=["POST"])
@require_tenant_access()
def verify_property_auto(tenant_id, property_id):
    """Automatically verify a property against its adagents.json file."""
    try:
        logger.info(f"🚀 Verify property request - tenant: {tenant_id}, property: {property_id}")

        # In production, always construct agent URL from tenant context
        # Dev overrides only allowed in development
        is_production = os.environ.get("PRODUCTION") == "true"
        logger.info(f"🏭 Environment: {'PRODUCTION' if is_production else 'DEVELOPMENT'}")

        if is_production:
            # Production: ignore any dev overrides, always use tenant context
            logger.info("🔒 Production mode: ignoring any dev URL overrides")
            agent_url = _construct_agent_url(tenant_id, request)
            logger.info(f"🏢 Constructed agent URL from tenant context: {agent_url}")
        else:
            # Development: allow dev overrides from form
            dev_url = request.form.get("dev_agent_url", "").strip()
            explicit_url = request.form.get("agent_url", "").strip()
            logger.info(f"🛠️ Development mode - dev_url: '{dev_url}', explicit_url: '{explicit_url}'")

            agent_url = dev_url or explicit_url
            if not agent_url:
                agent_url = _construct_agent_url(tenant_id, request)
                logger.info(f"🏗️ No override provided, constructed from tenant: {agent_url}")
            else:
                logger.info(f"🔧 Using override URL: {agent_url}")

        verification_service = get_property_verification_service()
        is_verified, error_message = verification_service.verify_property(tenant_id, property_id, agent_url)

        if is_verified:
            flash("Property verified successfully", "success")
        else:
            flash(f"Property verification failed: {error_message}", "error")

    except Exception as e:
        logger.error(f"Error verifying property: {e}")
        flash(f"Error verifying property: {str(e)}", "error")

    return redirect(url_for("authorized_properties.list_authorized_properties", tenant_id=tenant_id))


@authorized_properties_bp.route("/<tenant_id>/authorized-properties/create", methods=["GET", "POST"])
@require_tenant_access()
def create_property(tenant_id):
    """Create a new authorized property."""
    if request.method == "GET":
        try:
            with get_db_session() as db_session:
                tenant = db_session.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
                if not tenant:
                    flash(PROPERTY_ERROR_MESSAGES["tenant_not_found"], "error")
                    return redirect(url_for("core.admin_dashboard"))

                # Get existing tags for this tenant
                existing_tags = db_session.query(PropertyTag).filter(PropertyTag.tenant_id == tenant_id).all()

                return render_template(
                    "property_form.html",
                    tenant=tenant,
                    existing_tags=existing_tags,
                    property=None,
                    mode="create",
                    session=session,
                    user=session.get("user"),
                )

        except Exception as e:
            logger.error(f"Error loading create property form: {e}")
            flash(f"Error loading form: {str(e)}", "error")
            return redirect(url_for("authorized_properties.list_authorized_properties", tenant_id=tenant_id))

    # Handle POST request (form submission)
    try:
        # Validate form data
        is_valid, error_message, parsed_data = _validate_property_form(request)
        if not is_valid:
            flash(error_message, "error")
            return redirect(request.url)

        property_type, name, publisher_domain, identifiers, tags = parsed_data

        # Generate unique property_id
        property_id = str(uuid.uuid4())

        with get_db_session() as db_session:
            # Create new property
            new_property = AuthorizedProperty(
                property_id=property_id,
                tenant_id=tenant_id,
                property_type=property_type,
                name=name,
                identifiers=identifiers,
                tags=tags,
                publisher_domain=publisher_domain,
                verification_status="pending",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )

            db_session.add(new_property)
            db_session.commit()

            flash(f"Property '{name}' created successfully", "success")
            return redirect(url_for("authorized_properties.list_authorized_properties", tenant_id=tenant_id))

    except Exception as e:
        logger.error(f"Error creating property: {e}")
        flash(f"Error creating property: {str(e)}", "error")
        return redirect(url_for("authorized_properties.list_authorized_properties", tenant_id=tenant_id))


@authorized_properties_bp.route("/<tenant_id>/authorized-properties/<property_id>/edit", methods=["GET", "POST"])
@require_tenant_access()
def edit_property(tenant_id, property_id):
    """Edit an existing authorized property."""
    if request.method == "GET":
        try:
            with get_db_session() as db_session:
                tenant = db_session.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
                if not tenant:
                    flash(PROPERTY_ERROR_MESSAGES["tenant_not_found"], "error")
                    return redirect(url_for("core.admin_dashboard"))

                # Get the property to edit
                property_obj = (
                    db_session.query(AuthorizedProperty)
                    .filter(
                        AuthorizedProperty.tenant_id == tenant_id,
                        AuthorizedProperty.property_id == property_id,
                    )
                    .first()
                )

                if not property_obj:
                    flash(PROPERTY_ERROR_MESSAGES["property_not_found"], "error")
                    return redirect(url_for("authorized_properties.list_authorized_properties", tenant_id=tenant_id))

                # Get existing tags for this tenant
                existing_tags = db_session.query(PropertyTag).filter(PropertyTag.tenant_id == tenant_id).all()

                return render_template(
                    "property_form.html",
                    tenant=tenant,
                    existing_tags=existing_tags,
                    property=property_obj,
                    mode="edit",
                    session=session,
                    user=session.get("user"),
                )

        except Exception as e:
            logger.error(f"Error loading edit property form: {e}")
            flash(f"Error loading form: {str(e)}", "error")
            return redirect(url_for("authorized_properties.list_authorized_properties", tenant_id=tenant_id))

    # Handle POST request (form submission)
    try:
        # Validate form data
        is_valid, error_message, parsed_data = _validate_property_form(request)
        if not is_valid:
            flash(error_message, "error")
            return redirect(request.url)

        property_type, name, publisher_domain, identifiers, tags = parsed_data

        with get_db_session() as db_session:
            # Get the property to update
            property_obj = (
                db_session.query(AuthorizedProperty)
                .filter(
                    AuthorizedProperty.tenant_id == tenant_id,
                    AuthorizedProperty.property_id == property_id,
                )
                .first()
            )

            if not property_obj:
                flash(PROPERTY_ERROR_MESSAGES["property_not_found"], "error")
                return redirect(url_for("authorized_properties.list_authorized_properties", tenant_id=tenant_id))

            # Update property fields
            property_obj.property_type = property_type
            property_obj.name = name
            property_obj.identifiers = identifiers
            property_obj.tags = tags
            property_obj.publisher_domain = publisher_domain
            property_obj.verification_status = "pending"  # Reset verification status
            property_obj.verification_checked_at = None
            property_obj.verification_error = None
            property_obj.updated_at = datetime.utcnow()

            db_session.commit()

            flash(f"Property '{name}' updated successfully", "success")
            return redirect(url_for("authorized_properties.list_authorized_properties", tenant_id=tenant_id))

    except Exception as e:
        logger.error(f"Error updating property: {e}")
        flash(f"Error updating property: {str(e)}", "error")
        return redirect(url_for("authorized_properties.list_authorized_properties", tenant_id=tenant_id))
