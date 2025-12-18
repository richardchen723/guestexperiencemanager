/**
 * Mobile Form Enhancements
 * Multi-step forms, inline validation, mobile optimizations
 */

class MobileFormStepper {
    constructor(formElement, options = {}) {
        this.form = formElement;
        this.steps = options.steps || this.detectSteps();
        this.currentStep = 1;
        this.totalSteps = this.steps.length;
        this.onStepChange = options.onStepChange || null;
        this.onComplete = options.onComplete || null;
        
        this.init();
    }
    
    /**
     * Detect form steps based on form groups
     */
    detectSteps() {
        const steps = [];
        const formGroups = this.form.querySelectorAll('.form-group, .form-row');
        
        // Group fields into logical steps
        let currentStepFields = [];
        let stepIndex = 0;
        
        formGroups.forEach((group, index) => {
            // Check if this group should start a new step
            const isStepBreak = group.classList.contains('step-break') || 
                               group.querySelector('[data-step]');
            
            if (isStepBreak && currentStepFields.length > 0) {
                steps.push({
                    index: stepIndex++,
                    fields: [...currentStepFields],
                    title: group.dataset.stepTitle || `Step ${stepIndex}`
                });
                currentStepFields = [];
            }
            
            currentStepFields.push(group);
        });
        
        // Add remaining fields as last step
        if (currentStepFields.length > 0) {
            steps.push({
                index: stepIndex++,
                fields: currentStepFields,
                title: `Step ${stepIndex}`
            });
        }
        
        // If no steps detected, create default steps
        if (steps.length === 0) {
            return this.createDefaultSteps(formGroups);
        }
        
        return steps;
    }
    
    /**
     * Create default steps (3 steps)
     */
    createDefaultSteps(formGroups) {
        const groups = Array.from(formGroups);
        const stepSize = Math.ceil(groups.length / 3);
        
        return [
            {
                index: 0,
                fields: groups.slice(0, stepSize),
                title: 'Basic Information'
            },
            {
                index: 1,
                fields: groups.slice(stepSize, stepSize * 2),
                title: 'Details'
            },
            {
                index: 2,
                fields: groups.slice(stepSize * 2),
                title: 'Additional Information'
            }
        ];
    }
    
    /**
     * Initialize form stepper
     */
    init() {
        if (this.totalSteps <= 1 || window.innerWidth >= 768) {
            // Don't use stepper on desktop or if only one step
            return;
        }
        
        this.createProgressIndicator();
        this.setupStepNavigation();
        this.showStep(1);
    }
    
    /**
     * Create progress indicator
     */
    createProgressIndicator() {
        const progressContainer = document.createElement('div');
        progressContainer.className = 'form-stepper-progress mobile-only';
        progressContainer.innerHTML = `
            <div class="form-stepper-progress-bar">
                <div class="form-stepper-progress-fill" style="width: ${(1 / this.totalSteps) * 100}%"></div>
            </div>
            <div class="form-stepper-progress-text">
                Step ${this.currentStep} of ${this.totalSteps}
            </div>
        `;
        
        this.progressContainer = progressContainer;
        this.progressFill = progressContainer.querySelector('.form-stepper-progress-fill');
        this.progressText = progressContainer.querySelector('.form-stepper-progress-text');
        
        this.form.insertBefore(progressContainer, this.form.firstChild);
    }
    
    /**
     * Setup step navigation buttons
     */
    setupStepNavigation() {
        // Find or create form actions
        let formActions = this.form.querySelector('.form-actions');
        if (!formActions) {
            formActions = document.createElement('div');
            formActions.className = 'form-actions';
            this.form.appendChild(formActions);
        }
        
        // Clear existing buttons
        formActions.innerHTML = '';
        
        // Previous button
        if (this.currentStep > 1) {
            const prevBtn = document.createElement('button');
            prevBtn.type = 'button';
            prevBtn.className = 'btn btn-secondary form-stepper-prev';
            prevBtn.textContent = 'Previous';
            prevBtn.addEventListener('click', () => this.previousStep());
            formActions.appendChild(prevBtn);
        }
        
        // Next/Save button
        const nextBtn = document.createElement('button');
        nextBtn.type = this.currentStep === this.totalSteps ? 'submit' : 'button';
        nextBtn.className = 'btn btn-primary form-stepper-next';
        nextBtn.textContent = this.currentStep === this.totalSteps ? 'Save' : 'Next';
        if (nextBtn.type === 'button') {
            nextBtn.addEventListener('click', () => this.nextStep());
        }
        formActions.appendChild(nextBtn);
        
        this.formActions = formActions;
    }
    
    /**
     * Show specific step
     */
    showStep(stepNumber) {
        if (stepNumber < 1 || stepNumber > this.totalSteps) return;
        
        this.currentStep = stepNumber;
        
        // Hide all fields
        this.steps.forEach((step, index) => {
            step.fields.forEach(field => {
                if (index + 1 === stepNumber) {
                    field.style.display = '';
                } else {
                    field.style.display = 'none';
                }
            });
        });
        
        // Update progress
        this.updateProgress();
        
        // Update navigation buttons
        this.setupStepNavigation();
        
        // Scroll to top
        window.scrollTo({ top: 0, behavior: 'smooth' });
        
        if (this.onStepChange) {
            this.onStepChange(stepNumber, this.totalSteps);
        }
    }
    
    /**
     * Go to next step
     */
    nextStep() {
        if (this.validateCurrentStep()) {
            if (this.currentStep < this.totalSteps) {
                this.showStep(this.currentStep + 1);
            } else {
                // Last step - submit form
                if (this.onComplete) {
                    this.onComplete();
                } else {
                    this.form.dispatchEvent(new Event('submit'));
                }
            }
        }
    }
    
    /**
     * Go to previous step
     */
    previousStep() {
        if (this.currentStep > 1) {
            this.showStep(this.currentStep - 1);
        }
    }
    
    /**
     * Validate current step
     */
    validateCurrentStep() {
        const currentStepData = this.steps[this.currentStep - 1];
        let isValid = true;
        
        currentStepData.fields.forEach(field => {
            const inputs = field.querySelectorAll('input[required], select[required], textarea[required]');
            inputs.forEach(input => {
                if (!this.validateField(input)) {
                    isValid = false;
                }
            });
        });
        
        return isValid;
    }
    
    /**
     * Validate single field
     */
    validateField(field) {
        const value = field.value.trim();
        const isRequired = field.hasAttribute('required');
        let isValid = true;
        let errorMessage = '';
        
        // Remove existing error
        this.clearFieldError(field);
        
        // Required validation
        if (isRequired && !value) {
            isValid = false;
            errorMessage = 'This field is required';
        }
        
        // Type-specific validation
        if (value && field.type === 'email' && !this.isValidEmail(value)) {
            isValid = false;
            errorMessage = 'Please enter a valid email address';
        }
        
        if (value && field.type === 'url' && !this.isValidUrl(value)) {
            isValid = false;
            errorMessage = 'Please enter a valid URL';
        }
        
        // Show error if invalid
        if (!isValid) {
            this.showFieldError(field, errorMessage);
        }
        
        return isValid;
    }
    
    /**
     * Show field error
     */
    showFieldError(field, message) {
        field.classList.add('form-input-error');
        
        const errorEl = document.createElement('div');
        errorEl.className = 'mobile-form-error';
        errorEl.textContent = message;
        field.parentNode.appendChild(errorEl);
    }
    
    /**
     * Clear field error
     */
    clearFieldError(field) {
        field.classList.remove('form-input-error');
        const errorEl = field.parentNode.querySelector('.mobile-form-error');
        if (errorEl) {
            errorEl.remove();
        }
    }
    
    /**
     * Update progress indicator
     */
    updateProgress() {
        if (this.progressFill) {
            const percentage = (this.currentStep / this.totalSteps) * 100;
            this.progressFill.style.width = `${percentage}%`;
        }
        
        if (this.progressText) {
            this.progressText.textContent = `Step ${this.currentStep} of ${this.totalSteps}`;
        }
    }
    
    /**
     * Email validation
     */
    isValidEmail(email) {
        return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
    }
    
    /**
     * URL validation
     */
    isValidUrl(url) {
        try {
            new URL(url);
            return true;
        } catch {
            return false;
        }
    }
}

// Setup mobile form enhancements
function setupMobileForms() {
    if (window.innerWidth < 768) {
        // Find all forms
        document.querySelectorAll('form').forEach(form => {
            // Check if form should use stepper (has many fields)
            const fieldCount = form.querySelectorAll('.form-group, .form-row, input, select, textarea').length;
            
            if (fieldCount > 5) {
                // Initialize stepper
                const stepper = new MobileFormStepper(form, {
                    onComplete: () => {
                        // Form will submit normally
                    }
                });
            }
            
            // Enhance all inputs
            form.querySelectorAll('input, select, textarea').forEach(input => {
                enhanceInputForMobile(input);
            });
        });
    }
}

/**
 * Enhance input for mobile
 */
function enhanceInputForMobile(input) {
    // Set proper input types
    if (input.type === 'text') {
        const name = input.name || input.id || '';
        if (name.includes('email') || name.includes('Email')) {
            input.type = 'email';
        } else if (name.includes('phone') || name.includes('Phone') || name.includes('tel')) {
            input.type = 'tel';
        } else if (name.includes('url') || name.includes('Url') || name.includes('website')) {
            input.type = 'url';
        } else if (name.includes('number') || name.includes('Number') || name.includes('count')) {
            input.type = 'number';
        }
    }
    
    // Add mobile classes
    input.classList.add('mobile-form-input');
    
    // Add real-time validation
    input.addEventListener('blur', () => {
        validateInput(input);
    });
    
    // Clear error on input
    input.addEventListener('input', () => {
        input.classList.remove('form-input-error');
        const error = input.parentNode.querySelector('.mobile-form-error');
        if (error) {
            error.remove();
        }
    });
}

/**
 * Validate input
 */
function validateInput(input) {
    const value = input.value.trim();
    const isRequired = input.hasAttribute('required');
    let isValid = true;
    let errorMessage = '';
    
    // Remove existing error
    const existingError = input.parentNode.querySelector('.mobile-form-error');
    if (existingError) {
        existingError.remove();
    }
    input.classList.remove('form-input-error');
    
    // Required validation
    if (isRequired && !value) {
        isValid = false;
        errorMessage = 'This field is required';
    }
    
    // Type-specific validation
    if (value && input.type === 'email') {
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!emailRegex.test(value)) {
            isValid = false;
            errorMessage = 'Please enter a valid email address';
        }
    }
    
    if (value && input.type === 'url') {
        try {
            new URL(value);
        } catch {
            isValid = false;
            errorMessage = 'Please enter a valid URL';
        }
    }
    
    // Show error if invalid
    if (!isValid) {
        input.classList.add('form-input-error');
        const errorEl = document.createElement('div');
        errorEl.className = 'mobile-form-error';
        errorEl.textContent = errorMessage;
        input.parentNode.appendChild(errorEl);
    }
    
    return isValid;
}

// Initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupMobileForms);
} else {
    setupMobileForms();
}

// Re-initialize on resize
let resizeTimer;
window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(setupMobileForms, 250);
});

