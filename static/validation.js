// Form validation utilities for AdCP Admin UI

// Validation rules
const validators = {
    // Email validation
    email: (value) => {
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!emailRegex.test(value)) {
            return 'Please enter a valid email address';
        }
        return null;
    },

    // URL validation (for webhooks)
    url: (value) => {
        if (!value) return null; // Optional
        try {
            const url = new URL(value);
            if (!['http:', 'https:'].includes(url.protocol)) {
                return 'URL must start with http:// or https://';
            }
            return null;
        } catch (e) {
            return 'Please enter a valid URL';
        }
    },

    // Webhook URL validation (more specific)
    webhookUrl: (value) => {
        if (!value) return null; // Optional
        const urlError = validators.url(value);
        if (urlError) return urlError;

        // Check for common webhook patterns
        if (value.includes('hooks.slack.com/services/')) {
            // Slack webhook validation
            const parts = value.split('/');
            if (parts.length < 7) {
                return 'Invalid Slack webhook URL format';
            }
        }
        return null;
    },

    // JSON validation
    json: (value) => {
        try {
            JSON.parse(value);
            return null;
        } catch (e) {
            return `Invalid JSON: ${e.message}`;
        }
    },

    // Principal ID validation
    principalId: (value) => {
        if (!value) return 'Principal ID is required';
        if (!/^[a-zA-Z0-9_-]+$/.test(value)) {
            return 'Principal ID can only contain letters, numbers, underscores, and hyphens';
        }
        if (value.length < 3) {
            return 'Principal ID must be at least 3 characters long';
        }
        if (value.length > 50) {
            return 'Principal ID must be less than 50 characters';
        }
        return null;
    },

    // Network ID validation (numeric)
    networkId: (value) => {
        if (!value) return 'Network ID is required';
        if (!/^\d+$/.test(value)) {
            return 'Network ID must be numeric';
        }
        return null;
    },

    // Required field validation
    required: (value) => {
        if (!value || value.trim() === '') {
            return 'This field is required';
        }
        return null;
    },

    // Min length validation
    minLength: (min) => (value) => {
        if (value && value.length < min) {
            return `Must be at least ${min} characters`;
        }
        return null;
    },

    // Max length validation
    maxLength: (max) => (value) => {
        if (value && value.length > max) {
            return `Must be less than ${max} characters`;
        }
        return null;
    }
};

// Show error message for a field
function showError(fieldId, message) {
    const field = document.getElementById(fieldId);
    if (!field) return;

    // Remove existing error
    clearError(fieldId);

    // Add error class
    field.classList.add('error');

    // Create error message element
    const errorDiv = document.createElement('div');
    errorDiv.className = 'field-error';
    errorDiv.textContent = message;
    errorDiv.id = `${fieldId}-error`;

    // Insert after the field
    field.parentNode.insertBefore(errorDiv, field.nextSibling);
}

// Clear error message for a field
function clearError(fieldId) {
    const field = document.getElementById(fieldId);
    if (!field) return;

    field.classList.remove('error');

    const errorDiv = document.getElementById(`${fieldId}-error`);
    if (errorDiv) {
        errorDiv.remove();
    }
}

// Validate a single field
function validateField(fieldId, validatorFuncs) {
    const field = document.getElementById(fieldId);
    if (!field) return true;

    const value = field.value;

    for (const validator of validatorFuncs) {
        const error = validator(value);
        if (error) {
            showError(fieldId, error);
            return false;
        }
    }

    clearError(fieldId);
    return true;
}

// Validate entire form
function validateForm(formId, fieldValidators) {
    let isValid = true;

    for (const [fieldId, validators] of Object.entries(fieldValidators)) {
        if (!validateField(fieldId, validators)) {
            isValid = false;
        }
    }

    return isValid;
}

// Add real-time validation to a field
function addRealtimeValidation(fieldId, validatorFuncs) {
    const field = document.getElementById(fieldId);
    if (!field) return;

    // Validate on blur
    field.addEventListener('blur', () => {
        validateField(fieldId, validatorFuncs);
    });

    // Clear error on input (gives immediate feedback when user fixes error)
    field.addEventListener('input', () => {
        const hasError = field.classList.contains('error');
        if (hasError) {
            validateField(fieldId, validatorFuncs);
        }
    });
}

// Initialize validation for Slack form
function initSlackFormValidation() {
    const form = document.getElementById('slackForm');
    if (!form) return;

    // Add real-time validation
    addRealtimeValidation('slack_webhook_url', [validators.webhookUrl]);
    addRealtimeValidation('slack_audit_webhook_url', [validators.webhookUrl]);

    // Validate on submit
    form.addEventListener('submit', (e) => {
        const isValid = validateForm('slackForm', {
            'slack_webhook_url': [validators.webhookUrl],
            'slack_audit_webhook_url': [validators.webhookUrl]
        });

        if (!isValid) {
            e.preventDefault();
        }
    });
}

// Initialize validation for JSON config form
function initConfigFormValidation() {
    const configField = document.getElementById('config');
    if (!configField) return;

    // Add real-time validation
    addRealtimeValidation('config', [validators.required, validators.json]);

    // Format JSON on blur
    configField.addEventListener('blur', () => {
        try {
            const parsed = JSON.parse(configField.value);
            configField.value = JSON.stringify(parsed, null, 2);
        } catch (e) {
            // Invalid JSON, validation will handle the error
        }
    });
}

// Initialize validation for principal form
function initPrincipalFormValidation() {
    const form = document.querySelector('form[action*="/principals/create"]');
    if (!form) return;

    // Add real-time validation
    addRealtimeValidation('principal_id', [validators.required, validators.principalId]);
    addRealtimeValidation('name', [
        validators.required,
        validators.minLength(3),
        validators.maxLength(100)
    ]);

    // Validate on submit
    form.addEventListener('submit', (e) => {
        const isValid = validateForm('principalForm', {
            'principal_id': [validators.required, validators.principalId],
            'name': [validators.required, validators.minLength(3), validators.maxLength(100)]
        });

        if (!isValid) {
            e.preventDefault();
        }
    });
}

// Initialize validation for adapter setup forms
function initAdapterFormValidation() {
    // GAM validation
    addRealtimeValidation('gam_network_id', [validators.required, validators.networkId]);
    addRealtimeValidation('gam_credentials', [validators.required, validators.json]);

    // Triton validation
    addRealtimeValidation('triton_station_id', [validators.required, validators.minLength(3)]);
    addRealtimeValidation('triton_api_key', [validators.required, validators.minLength(10)]);
}

// Initialize validation for user form
function initUserFormValidation() {
    const form = document.querySelector('.add-user-form');
    if (!form) return;

    // Add email validation
    const emailInput = form.querySelector('input[type="email"]');
    if (emailInput) {
        addRealtimeValidation(emailInput.id, [validators.required, validators.email]);
    }
}

// Initialize all validations when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    initSlackFormValidation();
    initConfigFormValidation();
    initPrincipalFormValidation();
    initAdapterFormValidation();
    initUserFormValidation();
});

// Export for use in other scripts
window.FormValidation = {
    validators,
    validateField,
    validateForm,
    showError,
    clearError,
    addRealtimeValidation
};
