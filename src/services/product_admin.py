"""Product admin service — business logic for product CRUD.

Extracted from src/admin/blueprints/products.py Flask blueprint.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select

from src.core.database.database_session import get_db_session
from src.core.database.models import PricingOption, Product
from src.core.exceptions import AdCPNotFoundError, AdCPValidationError

logger = logging.getLogger(__name__)


class ProductAdminService:
    """Stateless service for product CRUD operations."""

    def list_products(self, tenant_id: str) -> dict[str, Any]:
        """List all products for a tenant."""
        with get_db_session() as session:
            stmt = select(Product).filter_by(tenant_id=tenant_id).order_by(Product.name)
            products = session.scalars(stmt).all()
            return {
                "products": [self._to_dict(p, session) for p in products],
                "count": len(products),
            }

    def get_product(self, tenant_id: str, product_id: str) -> dict[str, Any]:
        """Get a single product with full details."""
        with get_db_session() as session:
            stmt = select(Product).filter_by(tenant_id=tenant_id, product_id=product_id)
            product = session.scalars(stmt).first()
            if not product:
                raise AdCPNotFoundError(f"Product '{product_id}' not found")
            return self._to_dict(product, session)

    def create_product(self, tenant_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new product."""
        name = data.get("name", "")
        if not name:
            raise AdCPValidationError("Product name is required")

        product_id = data.get("product_id") or f"prod_{uuid.uuid4().hex[:8]}"

        with get_db_session() as session:
            # Check for duplicate
            stmt = select(Product).filter_by(tenant_id=tenant_id, product_id=product_id)
            if session.scalars(stmt).first():
                raise AdCPValidationError(f"Product '{product_id}' already exists")

            product = Product(
                tenant_id=tenant_id,
                product_id=product_id,
                name=name,
                description=data.get("description"),
                delivery_type=data.get("delivery_type", "guaranteed"),
                format_ids=data.get("format_ids", []),
                targeting_template=data.get("targeting_template", {}),
                property_ids=data.get("property_ids"),
                property_tags=data.get("property_tags"),
                channels=data.get("channels"),
                countries=data.get("countries"),
                measurement=data.get("measurement"),
                creative_policy=data.get("creative_policy"),
                implementation_config=data.get("implementation_config"),
                inventory_profile_id=data.get("inventory_profile_id"),
            )
            session.add(product)

            # Create pricing options
            for po_data in data.get("pricing_options", []):
                po = PricingOption(
                    tenant_id=tenant_id,
                    product_id=product_id,
                    pricing_model=po_data["pricing_model"],
                    currency=po_data.get("currency", "USD"),
                    is_fixed=po_data.get("is_fixed", True),
                    rate=Decimal(str(po_data["rate"])) if po_data.get("rate") is not None else None,
                    price_guidance=po_data.get("price_guidance"),
                    parameters=po_data.get("parameters"),
                    min_spend_per_package=(
                        Decimal(str(po_data["min_spend_per_package"]))
                        if po_data.get("min_spend_per_package") is not None
                        else None
                    ),
                )
                session.add(po)

            session.commit()
            session.refresh(product)
            return self._to_dict(product, session)

    def update_product(self, tenant_id: str, product_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update an existing product."""
        with get_db_session() as session:
            stmt = select(Product).filter_by(tenant_id=tenant_id, product_id=product_id)
            product = session.scalars(stmt).first()
            if not product:
                raise AdCPNotFoundError(f"Product '{product_id}' not found")

            # Simple field updates
            for field in ("name", "description", "delivery_type"):
                if field in data:
                    setattr(product, field, data[field])

            # JSON field updates
            for field in (
                "format_ids",
                "targeting_template",
                "property_ids",
                "property_tags",
                "channels",
                "countries",
                "measurement",
                "creative_policy",
                "implementation_config",
            ):
                if field in data:
                    setattr(product, field, data[field])

            if "inventory_profile_id" in data:
                product.inventory_profile_id = data["inventory_profile_id"]

            # Replace pricing options if provided
            if "pricing_options" in data:
                session.execute(
                    delete(PricingOption).where(
                        PricingOption.tenant_id == tenant_id,
                        PricingOption.product_id == product_id,
                    )
                )
                for po_data in data["pricing_options"]:
                    po = PricingOption(
                        tenant_id=tenant_id,
                        product_id=product_id,
                        pricing_model=po_data["pricing_model"],
                        currency=po_data.get("currency", "USD"),
                        is_fixed=po_data.get("is_fixed", True),
                        rate=Decimal(str(po_data["rate"])) if po_data.get("rate") is not None else None,
                        price_guidance=po_data.get("price_guidance"),
                        parameters=po_data.get("parameters"),
                        min_spend_per_package=(
                            Decimal(str(po_data["min_spend_per_package"]))
                            if po_data.get("min_spend_per_package") is not None
                            else None
                        ),
                    )
                    session.add(po)

            session.commit()
            session.refresh(product)
            return self._to_dict(product, session)

    def delete_product(self, tenant_id: str, product_id: str) -> dict[str, Any]:
        """Delete a product and its pricing options."""
        with get_db_session() as session:
            stmt = select(Product).filter_by(tenant_id=tenant_id, product_id=product_id)
            product = session.scalars(stmt).first()
            if not product:
                raise AdCPNotFoundError(f"Product '{product_id}' not found")

            # Pricing options cascade-delete via relationship
            session.delete(product)
            session.commit()
            return {"message": f"Product '{product_id}' deleted", "product_id": product_id}

    def list_creative_formats(self, tenant_id: str) -> dict[str, Any]:
        """List available creative formats."""
        try:
            from src.core.format_resolver import list_available_formats

            formats = list_available_formats(tenant_id=tenant_id)
            return {
                "formats": [
                    {
                        "id": str(f.format_id.id) if hasattr(f, "format_id") else str(f),
                        "agent_url": str(f.format_id.agent_url) if hasattr(f, "format_id") else None,
                        "name": f.name if hasattr(f, "name") else str(f),
                    }
                    for f in formats
                ],
                "count": len(formats),
            }
        except (ImportError, Exception) as e:
            logger.warning(f"Could not list creative formats: {e}")
            return {"formats": [], "count": 0}

    def _to_dict(self, product: Product, session: Any) -> dict[str, Any]:
        """Convert Product ORM object to dict."""
        # Get pricing options
        po_stmt = select(PricingOption).filter_by(tenant_id=product.tenant_id, product_id=product.product_id)
        pricing_options = session.scalars(po_stmt).all()

        return {
            "tenant_id": product.tenant_id,
            "product_id": product.product_id,
            "name": product.name,
            "description": product.description,
            "delivery_type": product.delivery_type,
            "format_ids": product.format_ids if product.format_ids else [],
            "pricing_options": [
                {
                    "pricing_model": po.pricing_model,
                    "currency": po.currency,
                    "is_fixed": po.is_fixed,
                    "rate": float(po.rate) if po.rate is not None else None,
                    "price_guidance": po.price_guidance,
                    "parameters": po.parameters,
                    "min_spend_per_package": (
                        float(po.min_spend_per_package) if po.min_spend_per_package is not None else None
                    ),
                }
                for po in pricing_options
            ],
            "property_ids": product.property_ids if product.property_ids else None,
            "property_tags": product.property_tags if product.property_tags else None,
            "channels": product.channels if product.channels else None,
            "countries": product.countries if product.countries else None,
            "created_at": ts.isoformat() if (ts := getattr(product, "created_at", None)) else None,
            "updated_at": ts2.isoformat() if (ts2 := getattr(product, "updated_at", None)) else None,
        }
