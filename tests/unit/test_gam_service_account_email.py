"""``gam_service_account_email`` follows the stored GAM service-account key.

``tenant_status`` counts a GAM service-account tenant as configured only when both the key
and the email are stored, so the ``AdapterConfig.gam_service_account_json`` setter keeps
the pair in step for every writer (the admin paste path and GCP auto-provisioning), and
``validate_gam_config`` refuses a key whose ``client_email`` could not fill it.
"""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from src.admin.blueprints.gam import validate_gam_config
from src.core.database.models import AdapterConfig
from src.core.operator_errors import InvalidFormInput
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


def _service_account_form(document) -> dict:
    return {"auth_method": "service_account", "service_account_json": json.dumps(document)}


def test_a_complete_key_is_accepted():
    validate_gam_config(_service_account_form(_key()))


@pytest.mark.parametrize("client_email", ["", "   ", None, 5])
def test_a_key_with_an_empty_or_non_string_client_email_is_refused(client_email):
    with pytest.raises(InvalidFormInput) as refused:
        validate_gam_config(_service_account_form(_key(client_email=client_email)))

    assert [issue.field for issue in refused.value.issues] == ["service_account_json"]
    assert "client_email" in refused.value.issues[0].text


@pytest.mark.parametrize("document", [5, "x", [1]])
def test_a_key_that_is_not_a_json_object_is_refused(document):
    with pytest.raises(InvalidFormInput) as refused:
        validate_gam_config(_service_account_form(document))

    assert refused.value.issues[0].text == "Service account JSON must be a JSON object"
