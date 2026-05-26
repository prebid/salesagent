from unittest.mock import patch


def test_initialize_application_calls_init_telemetry():
    with (
        patch("src.core.startup.setup_structured_logging"),
        patch("src.core.startup.setup_oauth_logging"),
        patch("src.core.startup.validate_configuration"),
        patch("src.core.startup.init_telemetry") as mock_init_tel,
        patch("src.core.startup.instrument_sqlalchemy"),
    ):
        from src.core.startup import initialize_application

        initialize_application()
        mock_init_tel.assert_called_once_with()


def test_initialize_application_calls_instrument_sqlalchemy():
    with (
        patch("src.core.startup.setup_structured_logging"),
        patch("src.core.startup.setup_oauth_logging"),
        patch("src.core.startup.validate_configuration"),
        patch("src.core.startup.init_telemetry"),
        patch("src.core.startup.instrument_sqlalchemy") as mock_instrument,
    ):
        from src.core.startup import initialize_application

        initialize_application()
        mock_instrument.assert_called_once_with()
