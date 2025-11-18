#!/usr/bin/env python3
"""
Unit tests for core error handling logic in media buy approval.

This tests the fix for the bug where trying to approve a media buy would crash with:
"'CreateMediaBuyError' object has no attribute 'media_buy_id'"

These tests verify the isinstance() checks work correctly to distinguish between
CreateMediaBuyError and CreateMediaBuySuccess responses.
"""

from src.core.schemas import CreateMediaBuyError, CreateMediaBuySuccess, Error


class TestCreateMediaBuyErrorHandling:
    """Test core error handling logic for CreateMediaBuyError responses."""

    def test_isinstance_check_identifies_error_response(self):
        """Test that isinstance() correctly identifies CreateMediaBuyError."""
        error_response = CreateMediaBuyError(
            errors=[Error(code="VALIDATION_ERROR", message="Budget exceeds daily limit")]
        )

        # Verify it's identified as error, not success
        assert isinstance(error_response, CreateMediaBuyError)
        assert not isinstance(error_response, CreateMediaBuySuccess)

    def test_isinstance_check_identifies_success_response(self):
        """Test that isinstance() correctly identifies CreateMediaBuySuccess."""
        success_response = CreateMediaBuySuccess(media_buy_id="mb_123", buyer_ref="ref_123", packages=[])

        # Verify it's identified as success, not error
        assert isinstance(success_response, CreateMediaBuySuccess)
        assert not isinstance(success_response, CreateMediaBuyError)

    def test_error_response_has_errors_not_media_buy_id(self):
        """Test that CreateMediaBuyError has 'errors' field but not 'media_buy_id'.

        This was the root cause of the bug - code tried to access response.media_buy_id
        on a CreateMediaBuyError object, which doesn't have that attribute.
        """
        error_response = CreateMediaBuyError(
            errors=[
                Error(code="VALIDATION_ERROR", message="Budget exceeds daily limit"),
                Error(code="INVENTORY_ERROR", message="Requested inventory not available"),
            ]
        )

        # CreateMediaBuyError has 'errors' field
        assert hasattr(error_response, "errors")
        assert len(error_response.errors) == 2
        assert error_response.errors[0].code == "VALIDATION_ERROR"
        assert error_response.errors[1].code == "INVENTORY_ERROR"

        # CreateMediaBuyError does NOT have 'media_buy_id' field
        assert not hasattr(error_response, "media_buy_id")

    def test_success_response_has_media_buy_id(self):
        """Test that CreateMediaBuySuccess has 'media_buy_id' field."""
        success_response = CreateMediaBuySuccess(media_buy_id="mb_123", buyer_ref="ref_123", packages=[])

        # CreateMediaBuySuccess has 'media_buy_id' field
        assert hasattr(success_response, "media_buy_id")
        assert success_response.media_buy_id == "mb_123"

    def test_error_response_with_single_error(self):
        """Test CreateMediaBuyError with single error (AdCP spec requires min_length=1)."""
        error_response = CreateMediaBuyError(
            errors=[Error(code="INVALID_REQUEST", message="Single validation error")]
        )

        # Verify error response structure
        assert isinstance(error_response, CreateMediaBuyError)
        assert hasattr(error_response, "errors")
        assert len(error_response.errors) == 1
        assert error_response.errors[0].code == "INVALID_REQUEST"
        assert not hasattr(error_response, "media_buy_id")
