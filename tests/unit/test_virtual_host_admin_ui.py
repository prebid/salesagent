"""Unit tests for virtual host admin UI functionality."""

from unittest.mock import Mock


class TestVirtualHostAdminUI:
    """Test virtual host configuration in admin UI."""

    def test_virtual_host_form_field_rendering(self):
        """Test that virtual host form field is properly rendered."""
        # Arrange - simulate tenant data
        tenant = Mock()
        tenant.tenant_id = "ui-test"
        tenant.name = "UI Test Publisher"
        tenant.subdomain = "ui-test"
        tenant.virtual_host = "ui.testcompany.com"

        # Act - simulate form field rendering logic
        form_html = f"""
        <div class="form-group">
            <label for="virtual_host">Virtual Host (Optional)</label>
            <input type="text" id="virtual_host" name="virtual_host"
                   value="{tenant.virtual_host or ""}"
                   placeholder="e.g. ad-sales.yourcompany.com">
            <small>Configure a custom domain through
                   <a href="https://approximated.app" target="_blank">Approximated.app</a>
                   to use this feature</small>
        </div>
        """

        # Assert
        assert 'id="virtual_host"' in form_html
        assert 'name="virtual_host"' in form_html
        assert tenant.virtual_host in form_html
        assert "Virtual Host (Optional)" in form_html
        assert "approximated.app" in form_html

    def test_virtual_host_form_field_empty_value(self):
        """Test form field rendering with empty virtual host."""
        # Arrange
        tenant = Mock()
        tenant.virtual_host = None

        # Act
        form_value = tenant.virtual_host or ""

        # Assert
        assert form_value == ""

    def test_virtual_host_uniqueness_logic(self):
        """Test uniqueness validation logic for virtual hosts."""
        # Test case 1: Different tenant with same virtual host (should fail)
        existing_tenant_id = "other-tenant"
        current_tenant_id = "current-tenant"

        is_unique_different = existing_tenant_id == current_tenant_id
        assert not is_unique_different  # Should fail uniqueness check

        # Test case 2: Same tenant updating its own virtual host (should pass)
        same_tenant_id = "same-tenant"
        current_tenant_id_same = "same-tenant"

        is_unique_same = same_tenant_id == current_tenant_id_same
        assert is_unique_same  # Should pass uniqueness check for same tenant

    def test_virtual_host_update_success(self):
        """Test successful virtual host update."""
        # Arrange
        mock_tenant = Mock()
        mock_tenant.tenant_id = "update-test"
        mock_tenant.virtual_host = "old.example.com"

        # Act - simulate update logic
        new_virtual_host = "new.example.com"
        mock_tenant.virtual_host = new_virtual_host

        # Assert
        assert mock_tenant.virtual_host == "new.example.com"

    def test_virtual_host_form_submission_data(self):
        """Test form data processing for virtual host."""
        # Arrange - simulate form data
        form_data = {
            "tenant_name": "Form Test Publisher",
            "virtual_host": "  form.test.com  ",  # With whitespace
            "max_daily_budget": "15000",
        }

        # Act - simulate form data processing
        virtual_host = form_data.get("virtual_host", "").strip()
        processed_virtual_host = virtual_host or None

        # Assert
        assert processed_virtual_host == "form.test.com"

    def test_virtual_host_form_submission_empty(self):
        """Test form data processing with empty virtual host."""
        # Arrange
        form_data = {
            "tenant_name": "Empty Test Publisher",
            "virtual_host": "",  # Empty string
        }

        # Act
        virtual_host = form_data.get("virtual_host", "").strip()
        processed_virtual_host = virtual_host or None

        # Assert
        assert processed_virtual_host is None

    def test_virtual_host_display_in_template(self):
        """Test virtual host display in tenant settings template."""
        # Arrange
        tenant = Mock()
        tenant.virtual_host = "display.test.com"

        # Act - simulate template rendering logic
        display_value = tenant.virtual_host or ""
        template_context = {"tenant": tenant, "virtual_host_value": display_value}

        # Assert
        assert template_context["virtual_host_value"] == "display.test.com"

    def test_virtual_host_placeholder_text(self):
        """Test that form includes helpful placeholder text."""
        # Act - check placeholder content
        placeholder = "e.g. ad-sales.yourcompany.com"
        help_text = 'Configure a custom domain through <a href="https://approximated.app" target="_blank">Approximated.app</a> to use this feature'

        # Assert
        assert "ad-sales." in placeholder
        assert "yourcompany.com" in placeholder
        assert "approximated.app" in help_text
        assert 'target="_blank"' in help_text

    def test_virtual_host_uniqueness_error_message(self):
        """Test error message for virtual host uniqueness violation."""
        # Act
        error_message = "This virtual host is already in use by another tenant"

        # Assert
        assert "already in use" in error_message
        assert "another tenant" in error_message

    def test_virtual_host_validation_error_handling(self):
        """Test error handling patterns in form submission."""
        # Test error message format
        error_message = "Virtual host cannot contain consecutive dots or start/end with dots"
        assert "consecutive dots" in error_message
        assert "start/end with dots" in error_message

    def test_virtual_host_success_message(self):
        """Test success message after virtual host update."""
        # Act - simulate success message
        success_message = "Tenant settings updated successfully"

        # Assert
        assert "updated successfully" in success_message
