"""GAM services share the canonical SQLAlchemy engine."""

import importlib
import sys
from unittest.mock import sentinel


def test_gam_inventory_and_orders_services_share_canonical_engine():
    from unittest.mock import patch

    module_names = ["src.services.gam_inventory_service", "src.services.gam_orders_service"]
    for module_name in module_names:
        sys.modules.pop(module_name, None)

    try:
        with patch(
            "src.core.database.database_session.get_engine", return_value=sentinel.canonical_engine
        ) as get_engine:
            gam_inventory_service = importlib.import_module("src.services.gam_inventory_service")
            gam_orders_service = importlib.import_module("src.services.gam_orders_service")

            get_engine.assert_not_called()
            assert gam_inventory_service.get_service_engine() is sentinel.canonical_engine
            assert gam_orders_service.get_service_engine() is sentinel.canonical_engine
    finally:
        for module_name in module_names:
            sys.modules.pop(module_name, None)
