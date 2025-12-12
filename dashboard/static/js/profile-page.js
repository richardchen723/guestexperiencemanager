/**
 * Profile page JavaScript
 */

// E.164 phone number validation pattern
const PHONE_PATTERN = /^\+[1-9]\d{1,14}$/;

document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('profile-form');
    const whatsappNumberInput = document.getElementById('whatsapp-number');
    const whatsappNumberError = document.getElementById('whatsapp-number-error');
    const notificationsCheckbox = document.getElementById('whatsapp-notifications-enabled');
    const saveButton = document.getElementById('save-button');
    const successMessage = document.getElementById('success-message');
    const errorMessage = document.getElementById('error-message');
    
    // Load current profile data
    loadProfile();
    
    // Real-time phone number validation
    whatsappNumberInput.addEventListener('input', function() {
        validatePhoneNumber();
    });
    
    // Form submission
    form.addEventListener('submit', function(e) {
        e.preventDefault();
        saveProfile();
    });
    
    function loadProfile() {
        fetch('/auth/api/profile')
            .then(response => response.json())
            .then(data => {
                if (data.whatsapp_number) {
                    whatsappNumberInput.value = data.whatsapp_number;
                }
                notificationsCheckbox.checked = data.whatsapp_notifications_enabled;
            })
            .catch(error => {
                console.error('Error loading profile:', error);
                showError('Failed to load profile data');
            });
    }
    
    function validatePhoneNumber() {
        const value = whatsappNumberInput.value.trim();
        
        if (!value) {
            // Empty is valid (optional field)
            whatsappNumberError.textContent = '';
            whatsappNumberInput.classList.remove('error');
            return true;
        }
        
        if (!PHONE_PATTERN.test(value)) {
            whatsappNumberError.textContent = 'Invalid format. Use E.164 format (e.g., +14155552671)';
            whatsappNumberInput.classList.add('error');
            return false;
        }
        
        whatsappNumberError.textContent = '';
        whatsappNumberInput.classList.remove('error');
        return true;
    }
    
    function saveProfile() {
        // Validate before submitting
        if (!validatePhoneNumber()) {
            showError('Please fix the phone number format before saving');
            return;
        }
        
        // Disable save button
        saveButton.disabled = true;
        saveButton.textContent = 'Saving...';
        hideMessages();
        
        const data = {
            whatsapp_number: whatsappNumberInput.value.trim() || null,
            whatsapp_notifications_enabled: notificationsCheckbox.checked
        };
        
        fetch('/auth/api/profile', {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                showError(data.error);
            } else {
                showSuccess('Profile updated successfully');
            }
        })
        .catch(error => {
            console.error('Error saving profile:', error);
            showError('Failed to save profile. Please try again.');
        })
        .finally(() => {
            saveButton.disabled = false;
            saveButton.textContent = 'Save Changes';
        });
    }
    
    function showSuccess(message) {
        successMessage.textContent = message;
        successMessage.style.display = 'block';
        errorMessage.style.display = 'none';
        
        // Hide after 3 seconds
        setTimeout(() => {
            hideMessages();
        }, 3000);
    }
    
    function showError(message) {
        errorMessage.textContent = message;
        errorMessage.style.display = 'block';
        successMessage.style.display = 'none';
    }
    
    function hideMessages() {
        successMessage.style.display = 'none';
        errorMessage.style.display = 'none';
    }
});


