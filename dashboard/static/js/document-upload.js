/**
 * Document Upload Component
 * Handles document upload with drag-and-drop, listing/tag selection, and admin visibility toggle
 */

class DocumentUploader {
    constructor(container, options = {}) {
        this.container = typeof container === 'string' ? document.querySelector(container) : container;
        if (!this.container) {
            throw new Error('DocumentUploader: Container element not found');
        }
        
        this.options = {
            onUploadComplete: options.onUploadComplete || (() => {}),
            onUploadError: options.onUploadError || (() => {})
        };
        
        this.selectedFile = null;
        this.listings = [];
        this.isAdmin = this.checkIsAdmin();
        
        // Load listings and admin status, then render
        this.loadListings().then(() => {
            this.render();
        });
    }
    
    checkIsAdmin() {
        // Check if current user is admin
        // This will be updated via API call in loadListings()
        // Also check if window.currentUserIsAdmin is available (set in template)
        return this._parseAdminStatus(window.currentUserIsAdmin);
    }
    
    _parseAdminStatus(value) {
        // Handle both boolean and string 'true'/'false' from template
        if (typeof value === 'boolean') {
            return value;
        }
        if (typeof value === 'string') {
            return value.toLowerCase() === 'true';
        }
        return false;
    }
    
    async loadListings() {
        try {
            const response = await fetch('/api/listings');
            if (response.ok) {
                const data = await response.json();
                // API returns array directly, not wrapped in object
                this.listings = Array.isArray(data) ? data : (data.listings || data.properties || []);
                
                // If components are already initialized, update them with listings
                if (this.listingMultiSelect && this.listings.length > 0) {
                    // Re-initialize with updated listings
                    const listingsContainer = document.getElementById('documentListings');
                    if (listingsContainer) {
                        const selectedIds = this.listingMultiSelect.getSelectedListingIds();
                        listingsContainer.innerHTML = '';
                        this.listingMultiSelect = new ListingMultiSelect(listingsContainer, {
                            listings: this.listings,
                            selectedListingIds: selectedIds,
                            placeholder: 'Select properties (optional)'
                        });
                    }
                } else if (document.getElementById('documentListings') && document.getElementById('documentListings').parentElement?.style.display !== 'none') {
                    // If form fields are visible but components not initialized yet, initialize them
                    this.initializeComponents();
                }
            }
        } catch (error) {
            console.error('Error loading listings:', error);
        }
        
        // Check if user is admin
        try {
            const response = await fetch('/auth/api/profile');
            if (response.ok) {
                const data = await response.json();
                this.isAdmin = data.is_admin || false;
            } else {
                // Fallback to window variable if API fails
                this.isAdmin = this._parseAdminStatus(window.currentUserIsAdmin);
            }
        } catch (error) {
            // Fallback to window variable if API fails
            this.isAdmin = this._parseAdminStatus(window.currentUserIsAdmin);
        }
        
        // Always update visibility section when admin status is loaded
        this.updateVisibilitySection();
    }
    
    updateVisibilitySection() {
        const formFields = this.container.querySelector('.document-upload-fields');
        if (!formFields) {
            return;
        }
        
        // Find or create visibility section
        let visibilityGroup = formFields.querySelector('.visibility-group');
        if (!visibilityGroup) {
            // Create visibility group before form actions
            const formActions = formFields.querySelector('.form-actions');
            if (!formActions) {
                return;
            }
            visibilityGroup = document.createElement('div');
            visibilityGroup.className = 'form-group visibility-group';
            formFields.insertBefore(visibilityGroup, formActions);
        }
        
        if (this.isAdmin) {
            visibilityGroup.innerHTML = `
                <label class="form-label">Visibility</label>
                <div class="visibility-options">
                    <label class="radio-label">
                        <input type="radio" name="visibility" value="all" checked>
                        <span>All Users</span>
                    </label>
                    <label class="radio-label">
                        <input type="radio" name="visibility" value="admin">
                        <span>Admins Only</span>
                    </label>
                </div>
            `;
            visibilityGroup.style.display = 'block';
        } else {
            visibilityGroup.style.display = 'none';
        }
    }
    
    render() {
        this.container.innerHTML = '';
        
        // File upload area
        const uploadArea = document.createElement('div');
        uploadArea.className = 'document-upload-area';
        uploadArea.innerHTML = `
            <div class="document-upload-dropzone" id="uploadDropzone">
                <input type="file" id="fileInput" accept=".pdf,.doc,.docx" style="display: none;">
                <div class="upload-icon">📄</div>
                <p class="upload-text">Drag and drop a document here, or <a href="#" id="browseLink">browse</a></p>
                <p class="upload-hint">Supported formats: PDF, Word (.doc, .docx). Max size: 25MB</p>
            </div>
            <div id="filePreview" class="file-preview" style="display: none;"></div>
        `;
        this.container.appendChild(uploadArea);
        
        // Form fields
        const formFields = document.createElement('div');
        formFields.className = 'document-upload-fields';
        formFields.style.display = 'none';
        formFields.innerHTML = `
            <div class="form-group">
                <label for="documentTitle" class="form-label">Document Title</label>
                <input type="text" id="documentTitle" class="form-input" placeholder="Enter document title (optional)">
            </div>
            
            <div class="form-group">
                <label for="documentListings" class="form-label">Properties (Optional)</label>
                <div id="documentListings" class="listing-multiselect-container"></div>
            </div>
            
            <div class="form-group">
                <label for="documentTags" class="form-label">Tags (Optional)</label>
                <div id="documentTags" class="tag-input-container"></div>
            </div>
            
            <div class="form-group visibility-group" style="display: none;">
                <!-- Visibility section will be populated dynamically when admin status is loaded -->
            </div>
            
            <div class="form-actions">
                <button type="button" id="uploadBtn" class="btn btn-primary">Upload Document</button>
                <button type="button" id="cancelBtn" class="btn btn-secondary">Cancel</button>
            </div>
        `;
        this.container.appendChild(formFields);
        
        // Update visibility section after admin status is loaded
        this.updateVisibilitySection();
        
        this.setupEventListeners();
    }
    
    setupEventListeners() {
        const dropzone = document.getElementById('uploadDropzone');
        const fileInput = document.getElementById('fileInput');
        const browseLink = document.getElementById('browseLink');
        const uploadBtn = document.getElementById('uploadBtn');
        const cancelBtn = document.getElementById('cancelBtn');
        const formFields = this.container.querySelector('.document-upload-fields');
        
        // Browse link
        if (browseLink) {
            browseLink.addEventListener('click', (e) => {
                e.preventDefault();
                fileInput.click();
            });
        }
        
        // File input change
        if (fileInput) {
            fileInput.addEventListener('change', (e) => {
                if (e.target.files.length > 0) {
                    this.handleFileSelect(e.target.files[0]);
                }
            });
        }
        
        // Drag and drop
        if (dropzone) {
            dropzone.addEventListener('dragover', (e) => {
                e.preventDefault();
                dropzone.classList.add('drag-over');
            });
            
            dropzone.addEventListener('dragleave', () => {
                dropzone.classList.remove('drag-over');
            });
            
            dropzone.addEventListener('drop', (e) => {
                e.preventDefault();
                dropzone.classList.remove('drag-over');
                
                if (e.dataTransfer.files.length > 0) {
                    this.handleFileSelect(e.dataTransfer.files[0]);
                }
            });
        }
        
        // Upload button
        if (uploadBtn) {
            uploadBtn.addEventListener('click', () => {
                this.uploadDocument();
            });
        }
        
        // Cancel button
        if (cancelBtn) {
            cancelBtn.addEventListener('click', () => {
                this.reset();
            });
        }
        
        // File remove button
        const fileRemoveBtn = document.getElementById('fileRemoveBtn');
        if (fileRemoveBtn) {
            fileRemoveBtn.addEventListener('click', () => {
                this._handleFileRemove();
            });
        }
    }
    
    handleFileSelect(file) {
        // Validate file
        const validationError = this._validateFile(file);
        if (validationError) {
            this.showMessage(validationError, 'error');
            return;
        }
        
        this.selectedFile = file;
        this.showFilePreview(file);
        this.showFormFields();
    }
    
    _validateFile(file) {
        if (!file) {
            return 'No file selected';
        }
        
        const ALLOWED_TYPES = [
            'application/pdf',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        ];
        const ALLOWED_EXTENSIONS = ['.pdf', '.doc', '.docx'];
        const MAX_SIZE = 25 * 1024 * 1024; // 25MB
        
        const fileExt = '.' + file.name.split('.').pop().toLowerCase();
        
        if (!ALLOWED_EXTENSIONS.includes(fileExt) && !ALLOWED_TYPES.includes(file.type)) {
            return 'Invalid file type. Please upload a PDF or Word document (.pdf, .doc, .docx)';
        }
        
        if (file.size > MAX_SIZE) {
            return `File size exceeds ${MAX_SIZE / (1024 * 1024)}MB limit`;
        }
        
        if (file.size === 0) {
            return 'File is empty';
        }
        
        return null;
    }
    
    showFilePreview(file) {
        const preview = document.getElementById('filePreview');
        if (!preview) return;
        
        const fileSize = (file.size / (1024 * 1024)).toFixed(2);
        preview.innerHTML = `
            <div class="file-preview-content">
                <div class="file-icon">📄</div>
                <div class="file-info">
                    <div class="file-name">${this.escapeHtml(file.name)}</div>
                    <div class="file-size">${fileSize} MB</div>
                </div>
                <button type="button" class="file-remove" id="fileRemoveBtn">×</button>
            </div>
        `;
        preview.style.display = 'block';
    }
    
    showFormFields() {
        const formFields = this.container.querySelector('.document-upload-fields');
        if (formFields) {
            formFields.style.display = 'block';
            
            // Update visibility section when form fields are shown
            this.updateVisibilitySection();
            
            // Wait a bit for DOM to be ready, then initialize components
            setTimeout(() => {
                this.initializeComponents();
            }, 100);
            
            // Set default title
            const titleInput = document.getElementById('documentTitle');
            if (titleInput && this.selectedFile) {
                // Remove extension for default title
                const fileName = this.selectedFile.name;
                const nameWithoutExt = fileName.substring(0, fileName.lastIndexOf('.')) || fileName;
                titleInput.value = nameWithoutExt;
            }
        }
    }
    
    initializeComponents() {
        // Initialize listing multi-select
        const listingsContainer = document.getElementById('documentListings');
        if (listingsContainer) {
            if (typeof ListingMultiSelect === 'undefined') {
                this._showComponentError(listingsContainer, 'Property selector not available. Please refresh the page.');
            } else {
                // Clear container
                listingsContainer.innerHTML = '';
                
                // Create new instance
                try {
                    this.listingMultiSelect = new ListingMultiSelect(listingsContainer, {
                        listings: this.listings || [],
                        placeholder: 'Select properties (optional)'
                    });
                } catch (error) {
                    this._showComponentError(listingsContainer, 'Error initializing property selector.');
                }
            }
        }
        
        // Initialize tag input
        const tagsContainer = document.getElementById('documentTags');
        if (tagsContainer) {
            if (typeof TagInput === 'undefined') {
                this._showComponentError(tagsContainer, 'Tag input not available. Please refresh the page.');
            } else {
                // Clear container
                tagsContainer.innerHTML = '';
                
                // Create new instance
                try {
                    this.tagInput = new TagInput(tagsContainer, {
                        existingTags: []
                    });
                } catch (error) {
                    this._showComponentError(tagsContainer, 'Error initializing tag input.');
                }
            }
        }
    }
    
    async uploadDocument() {
        if (!this.selectedFile) {
            this.showMessage('Please select a file first', 'error');
            return;
        }
        
        // Re-validate file before upload
        const validationError = this._validateFile(this.selectedFile);
        if (validationError) {
            this.showMessage(validationError, 'error');
            return;
        }
        
        const uploadBtn = document.getElementById('uploadBtn');
        const originalText = uploadBtn ? uploadBtn.textContent : '';
        
        try {
            if (uploadBtn) {
                uploadBtn.disabled = true;
                uploadBtn.textContent = 'Uploading...';
            }
            
            const formData = this._buildFormData();
            
            const response = await fetch('/knowledge/api/documents', {
                method: 'POST',
                body: formData
            });
            
            if (!response.ok) {
                let errorMessage = 'Upload failed';
                try {
                    const error = await response.json();
                    errorMessage = error.error || errorMessage;
                } catch (e) {
                    // If response is not JSON, use status text
                    errorMessage = response.statusText || errorMessage;
                }
                throw new Error(errorMessage);
            }
            
            const data = await response.json();
            
            // Reset form
            this.reset();
            
            // Show success message
            this.showMessage('Document uploaded successfully!', 'success');
            
            // Callback
            if (this.options.onUploadComplete) {
                this.options.onUploadComplete(data);
            }
            
            // Reload documents list if function exists
            if (typeof loadDocumentsList === 'function') {
                loadDocumentsList();
            }
            
        } catch (error) {
            const errorMessage = error.message || 'Failed to upload document. Please try again.';
            this.showMessage(errorMessage, 'error');
            
            if (this.options.onUploadError) {
                this.options.onUploadError(error);
            }
        } finally {
            if (uploadBtn) {
                uploadBtn.disabled = false;
                uploadBtn.textContent = originalText;
            }
        }
    }
    
    _buildFormData() {
        const formData = new FormData();
        formData.append('document', this.selectedFile);
        
        // Get title
        const titleInput = document.getElementById('documentTitle');
        if (titleInput && titleInput.value.trim()) {
            const title = titleInput.value.trim();
            // Sanitize title (basic length check)
            if (title.length > 500) {
                throw new Error('Document title is too long (max 500 characters)');
            }
            formData.append('title', title);
        }
        
        // Get listing IDs
        if (this.listingMultiSelect) {
            const listingIds = this.listingMultiSelect.getSelectedListingIds();
            listingIds.forEach(id => {
                // Validate listing ID is a number
                if (Number.isInteger(id) && id > 0) {
                    formData.append('listing_ids', id);
                }
            });
        }
        
        // Get tags
        if (this.tagInput) {
            const tags = this.tagInput.getTags() || [];
            tags.forEach(tag => {
                const tagName = tag.name || tag;
                if (tagName && typeof tagName === 'string') {
                    // Sanitize tag name (basic length check)
                    const sanitizedTag = tagName.trim().substring(0, 100);
                    if (sanitizedTag) {
                        formData.append('tag_names', sanitizedTag);
                    }
                }
            });
        }
        
        // Get visibility (admin only)
        if (this.isAdmin) {
            const visibilityRadio = this.container.querySelector('input[name="visibility"]:checked');
            if (visibilityRadio) {
                formData.append('is_admin_only', visibilityRadio.value === 'admin' ? 'true' : 'false');
            } else {
                formData.append('is_admin_only', 'false');
            }
        }
        
        return formData;
    }
    
    reset() {
        this.selectedFile = null;
        const fileInput = document.getElementById('fileInput');
        if (fileInput) {
            fileInput.value = '';
        }
        
        const preview = document.getElementById('filePreview');
        if (preview) {
            preview.style.display = 'none';
        }
        
        const formFields = this.container.querySelector('.document-upload-fields');
        if (formFields) {
            formFields.style.display = 'none';
        }
        
        const titleInput = document.getElementById('documentTitle');
        if (titleInput) {
            titleInput.value = '';
        }
        
        if (this.listingMultiSelect) {
            this.listingMultiSelect.setSelectedListingIds([]);
        }
        
        if (this.tagInput) {
            this.tagInput.setTags([]);
        }
    }
    
    showMessage(message, type) {
        // Create or update message element
        let messageEl = document.getElementById('uploadMessage');
        if (!messageEl) {
            messageEl = document.createElement('div');
            messageEl.id = 'uploadMessage';
            messageEl.className = `upload-message upload-message-${type}`;
            this.container.insertBefore(messageEl, this.container.firstChild);
        }
        
        messageEl.textContent = message;
        messageEl.className = `upload-message upload-message-${type}`;
        messageEl.style.display = 'block';
        
        // Auto-hide after 5 seconds
        setTimeout(() => {
            if (messageEl) {
                messageEl.style.display = 'none';
            }
        }, 5000);
    }
    
    _handleFileRemove() {
        const preview = document.getElementById('filePreview');
        if (preview) {
            preview.style.display = 'none';
        }
        
        const fileInput = document.getElementById('fileInput');
        if (fileInput) {
            fileInput.value = '';
        }
        
        this.selectedFile = null;
        
        const formFields = this.container.querySelector('.document-upload-fields');
        if (formFields) {
            formFields.style.display = 'none';
        }
    }
    
    _showComponentError(container, message) {
        if (container) {
            container.innerHTML = `<p style="color: var(--danger-color, #dc3545); padding: 0.5rem; margin: 0;">Error: ${this.escapeHtml(message)}</p>`;
        }
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

