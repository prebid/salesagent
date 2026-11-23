"""``gam_service_account_email`` follows the stored GAM service-account key.

``tenant_status`` counts a GAM service-account tenant as configured only when both the key
and the email are stored, so the ``AdapterConfig.gam_service_account_json`` setter keeps
the pair in step for every writer (the admin paste path and GCP auto-provisioning).
"""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from src.core.database.models import AdapterConfig
from tests.helpers.gam_credentials import service_account_key

CLIENT_EMAIL = "adcp-sales@example-project.iam.gserviceaccount.com"


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())


def _key(**overrides) -> dict:
    return {**service_account_key(client_email=CLIENT_EMAIL), **overrides}


def _config() -> AdapterConfig:
    return AdapterConfig(tenant_id="t1", adapter_type="google_ad_manager")


def test_storing_a_key_stores_its_client_email():
    config = _config()
    config.gam_service_account_json = json.dumps(_key())

    assert config.gam_service_account_email == CLIENT_EMAIL


@pytest.mark.parametrize("cleared", [None, ""])
def test_clearing_the_key_clears_the_email(cleared):
    config = _config()
    config.gam_service_account_json = json.dumps(_key())

    config.gam_service_account_json = cleared  # what configure_gam does on a switch to OAuth

    assert config._gam_service_account_json is None
    assert config.gam_service_account_email is None


def test_replacing_the_key_replaces_the_email():
    config = _config()
    config.gam_service_account_json = json.dumps(_key())

    config.gam_service_account_json = json.dumps(_key(client_email="other@p.iam.gserviceaccount.com"))

    assert config.gam_service_account_email == "other@p.iam.gserviceaccount.com"


@pytest.mark.parametrize("stored", ['{"type": "service_account"}', '{"client_email": ""}', "[1]", "not json"])
def test_a_key_without_a_usable_client_email_stores_no_email(stored):
    config = _config()
    config.gam_service_account_email = CLIENT_EMAIL

    config.gam_service_account_json = stored

    assert config.gam_service_account_json == stored
    assert config.gam_service_account_email is None
