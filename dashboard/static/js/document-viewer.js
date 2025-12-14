/**
 * Document Viewer Component
 * Modal viewer for document details and viewing with edit capabilities
 */

class DocumentViewer {
    static currentDocument = null;
    static listingMultiSelect = null;
    static tagInput = null;
    static listings = [];
    static autoSaveTimeout = null;
    
    static async open(documentId) {
        // Create modal if it doesn't exist
        let modal = document.getElementById('documentViewerModal');
        if (!modal) {
            modal = DocumentViewer.createModal();
            document.body.appendChild(modal);
        }
        
        // Reset edit mode
        DocumentViewer.isEditMode = false;
        DocumentViewer.listingMultiSelect = null;
        DocumentViewer.tagInput = null;
        
        // Reset delete button state before loading new document
        const deleteBtn = modal.querySelector('#deleteBtn');
        if (deleteBtn) {
            deleteBtn.disabled = false;
            deleteBtn.textContent = 'Delete Document';
        }
        
        // Load listings for edit mode
        await DocumentViewer.loadListings();
        
        // Load and display document
        await DocumentViewer.loadDocument(documentId, modal);
        
        // Show modal
        modal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    }
    
    static async loadListings() {
        try {
            const response = await fetch('/api/listings');
            if (response.ok) {
                const data = await response.json();
                DocumentViewer.listings = Array.isArray(data) ? data : (data.listings || []);
            }
        } catch (error) {
            console.error('Error loading listings:', error);
        }
    }
    
    static createModal() {
        const modal = document.createElement('div');
        modal.id = 'documentViewerModal';
        modal.className = 'document-viewer-modal';
        modal.style.display = 'none';
        
        modal.innerHTML = `
            <div class="document-viewer-overlay" onclick="DocumentViewer.close()"></div>
            <div class="document-viewer-content">
                <div class="document-viewer-header">
                    <h2 id="viewerTitle">Document</h2>
                    <button class="document-viewer-close" onclick="DocumentViewer.close()">×</button>
                </div>
                <div class="document-viewer-body" id="viewerBody">
                    <p class="loading">Loading document...</p>
                </div>
                <div class="document-viewer-footer">
                    <div class="footer-left">
                        <button id="deleteBtn" class="btn btn-danger" onclick="DocumentViewer.deleteDocument()" style="display: none;">Delete Document</button>
                    </div>
                    <div class="footer-right">
                        <button id="downloadBtn" class="btn btn-secondary" onclick="DocumentViewer.download()">Download</button>
                        <button id="openBtn" class="btn btn-primary" onclick="DocumentViewer.openInBrowser()">Open in Browser</button>
                    </div>
                </div>
            </div>
        `;
        
        // Close on Escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && modal.style.display === 'flex') {
                DocumentViewer.close();
            }
        });
        
        return modal;
    }
    
    static async loadDocument(documentId, modal) {
        const viewerBody = modal.querySelector('#viewerBody');
        const viewerTitle = modal.querySelector('#viewerTitle');
        
        try {
            const response = await fetch(`/knowledge/api/documents/${documentId}`);
            if (!response.ok) {
                throw new Error('Failed to load document');
            }
            
            const docData = await response.json();
            
            // Store document data
            DocumentViewer.currentDocument = docData;
            modal.dataset.documentId = documentId;
            
            // Update title
            if (viewerTitle) {
                viewerTitle.textContent = docData.title || 'Document';
            }
            
            // Display document info
            DocumentViewer.renderDocumentInfo(viewerBody, docData);
            
            // Show/hide delete button based on permissions
            DocumentViewer.updateDeleteButton(modal, docData);
            
        } catch (error) {
            console.error('Error loading document:', error);
            if (viewerBody) {
                viewerBody.innerHTML = '<p class="error">Failed to load document details.</p>';
            }
        }
    }
    
    static updateDeleteButton(modal, docData) {
        const deleteBtn = modal.querySelector('#deleteBtn');
        if (!deleteBtn) return;
        
        // Reset button state first
        deleteBtn.disabled = false;
        deleteBtn.textContent = 'Delete Document';
        
        // Show delete button if user is admin or document owner
        const isAdmin = window.currentUserIsAdmin === true || window.currentUserIsAdmin === 'true';
        const isOwner = docData.uploaded_by === (window.currentUserId || null);
        const canDelete = isAdmin || isOwner;
        
        if (canDelete) {
            deleteBtn.style.display = 'block';
        } else {
            deleteBtn.style.display = 'none';
        }
    }
    
    static renderDocumentInfo(viewerBody, docData) {
        if (!viewerBody) return;
        
        // Check if user can edit (admin or document owner)
        const canEdit = docData.uploaded_by === (window.currentUserId || null) || (window.currentUserIsAdmin || false);
        
        if (!canEdit) {
            // If user can't edit, show read-only view
            viewerBody.innerHTML = `
                <div class="document-info">
                    <div class="document-info-section">
                        <div class="info-group">
                            <div class="info-item">
                                <span class="info-label">File Name</span>
                                <span class="info-value">${this.escapeHtml(docData.file_name)}</span>
                            </div>
                            <div class="info-item">
                                <span class="info-label">File Size</span>
                                <span class="info-value">${this.formatFileSize(docData.file_size)}</span>
                            </div>
                            <div class="info-item">
                                <span class="info-label">File Type</span>
                                <span class="info-value">${this.escapeHtml(this.getFileTypeLabel(docData.mime_type))}</span>
                            </div>
                            <div class="info-item">
                                <span class="info-label">Uploaded</span>
                                <span class="info-value">${new Date(docData.created_at).toLocaleString()}</span>
                            </div>
                        </div>
                    </div>
                    
                    <div class="document-info-section">
                        <div class="info-section-header">
                            <svg class="info-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                                <path d="M8 1L1 4.5V7.5C1 11.5 4 14.5 8 15.5C12 14.5 15 11.5 15 7.5V4.5L8 1Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                                <path d="M5 8L7 10L11 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                            <span class="info-section-title">Associated Properties</span>
                            <span class="info-section-count">${docData.listings ? docData.listings.length : 0}</span>
                        </div>
                        <div class="document-listings-view">
                            ${docData.listings && docData.listings.length > 0 ? docData.listings.map(l => `
                                <div class="listing-chip">
                                    <svg class="chip-icon" width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
                                        <path d="M7 1L1 4V7C1 10.5 3.5 13.5 7 14.5C10.5 13.5 13 10.5 13 7V4L7 1Z" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>
                                    </svg>
                                    <span class="chip-text">${this.escapeHtml(l.internal_listing_name || l.name)}</span>
                                </div>
                            `).join('') : '<span class="empty-state">No properties associated</span>'}
                        </div>
                    </div>
                    
                    <div class="document-info-section">
                        <div class="info-section-header">
                            <svg class="info-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                                <path d="M2 2H14V14H2V2Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                                <path d="M6 6H10M6 10H10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                            </svg>
                            <span class="info-section-title">Tags</span>
                            <span class="info-section-count">${docData.tags ? docData.tags.length : 0}</span>
                        </div>
                        <div class="document-tags-view">
                            ${docData.tags && docData.tags.length > 0 ? docData.tags.map(t => `
                                <div class="tag-chip ${t.is_inherited ? 'tag-inherited' : ''}" style="background-color: ${t.color || '#2563eb'}20; border-color: ${t.color || '#2563eb'}; color: ${t.color || '#2563eb'};">
                                    <span class="chip-text">${this.escapeHtml(t.name)}</span>
                                    ${t.is_inherited ? '<span class="inherited-badge" title="Inherited from property">inherited</span>' : ''}
                                </div>
                            `).join('') : '<span class="empty-state">No tags</span>'}
                        </div>
                    </div>
                </div>
            `;
            return;
        }
        
        // Editable view - always show edit components
        viewerBody.innerHTML = `
            <div class="document-info">
                <div class="document-info-section">
                    <div class="info-group">
                        <div class="info-item">
                            <span class="info-label">File Name</span>
                            <span class="info-value">${this.escapeHtml(docData.file_name)}</span>
                        </div>
                        <div class="info-item">
                            <span class="info-label">File Size</span>
                            <span class="info-value">${this.formatFileSize(docData.file_size)}</span>
                        </div>
                        <div class="info-item">
                            <span class="info-label">File Type</span>
                            <span class="info-value">${this.escapeHtml(this.getFileTypeLabel(docData.mime_type))}</span>
                        </div>
                        <div class="info-item">
                            <span class="info-label">Uploaded</span>
                            <span class="info-value">${new Date(docData.created_at).toLocaleString()}</span>
                        </div>
                    </div>
                </div>
                
                <div class="document-info-section" id="listingsSection">
                    <div class="info-section-header">
                        <svg class="info-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M8 1L1 4.5V7.5C1 11.5 4 14.5 8 15.5C12 14.5 15 11.5 15 7.5V4.5L8 1Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                            <path d="M5 8L7 10L11 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                        <span class="info-section-title">Associated Properties</span>
                        <span class="info-section-count" id="listingsCount">${docData.listings ? docData.listings.length : 0}</span>
                    </div>
                    <div id="listingsEditContainer" class="listing-multiselect-container"></div>
                </div>
                
                <div class="document-info-section" id="tagsSection">
                    <div class="info-section-header">
                        <svg class="info-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M2 2H14V14H2V2Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                            <path d="M6 6H10M6 10H10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                        </svg>
                        <span class="info-section-title">Tags</span>
                        <span class="info-section-count" id="tagsCount">${docData.tags ? docData.tags.filter(t => !t.is_inherited).length : 0}</span>
                    </div>
                    <div id="tagsEditContainer" class="tag-input-container"></div>
                    <div class="edit-hint">Note: Tags inherited from properties cannot be edited here.</div>
                </div>
            </div>
        `;
        
        // Initialize edit components immediately
        setTimeout(() => {
            DocumentViewer.initializeEditComponents();
        }, 100);
    }
    
    static download() {
        const modal = document.getElementById('documentViewerModal');
        if (!modal || !modal.dataset.documentId) return;
        
        const documentId = modal.dataset.documentId;
        window.open(`/knowledge/api/documents/${documentId}/file?download=true`, '_blank');
    }
    
    static openInBrowser() {
        const modal = document.getElementById('documentViewerModal');
        if (!modal || !modal.dataset.documentId) return;
        
        const documentId = modal.dataset.documentId;
        window.open(`/knowledge/api/documents/${documentId}/file`, '_blank');
    }
    
    static initializeEditComponents() {
        // Initialize listing multi-select
        const listingsContainer = document.getElementById('listingsEditContainer');
        if (listingsContainer && typeof ListingMultiSelect !== 'undefined') {
            listingsContainer.innerHTML = '';
            const currentListingIds = DocumentViewer.currentDocument.listings 
                ? DocumentViewer.currentDocument.listings.map(l => l.listing_id)
                : [];
            
            DocumentViewer.listingMultiSelect = new ListingMultiSelect(listingsContainer, {
                listings: DocumentViewer.listings,
                selectedListingIds: currentListingIds,
                placeholder: 'Select properties (optional)',
                onSelectionChange: () => {
                    DocumentViewer.debouncedAutoSave();
                }
            });
        }
        
        // Initialize tag input (show all tags, but mark inherited ones as read-only)
        const tagsContainer = document.getElementById('tagsEditContainer');
        if (tagsContainer && typeof TagInput !== 'undefined') {
            tagsContainer.innerHTML = '';
            // Show all tags (both inherited and user-added)
            const allTags = DocumentViewer.currentDocument.tags
                ? DocumentViewer.currentDocument.tags.map(t => ({
                    tag_id: t.tag_id,
                    name: t.name,
                    color: t.color,
                    is_inherited: t.is_inherited || false
                }))
                : [];
            
            DocumentViewer.tagInput = new TagInput(tagsContainer, {
                existingTags: allTags,
                onTagsChange: () => {
                    DocumentViewer.debouncedAutoSave();
                }
            });
        }
    }
    
    static debouncedAutoSave() {
        // Clear existing timeout
        if (DocumentViewer.autoSaveTimeout) {
            clearTimeout(DocumentViewer.autoSaveTimeout);
        }
        
        // Set new timeout (500ms debounce)
        DocumentViewer.autoSaveTimeout = setTimeout(() => {
            DocumentViewer.autoSave();
        }, 500);
    }
    
    static async autoSave() {
        const modal = document.getElementById('documentViewerModal');
        if (!modal || !modal.dataset.documentId || !DocumentViewer.currentDocument) return;
        
        const documentId = modal.dataset.documentId;
        
        try {
            // Get listing IDs
            const listingIds = DocumentViewer.listingMultiSelect 
                ? DocumentViewer.listingMultiSelect.getSelectedListingIds()
                : [];
            
            // Get tag names (only non-inherited tags - inherited tags are automatically managed)
            const tags = DocumentViewer.tagInput 
                ? DocumentViewer.tagInput.getTags() || []
                : [];
            // Filter out inherited tags - only save user-added tags
            const tagNames = tags
                .filter(t => !t.is_inherited)
                .map(t => t.name || t)
                .filter(Boolean);
            
            // Prepare update data
            const updateData = {
                listing_ids: listingIds,
                tag_names: tagNames
            };
            
            // Send update request
            const response = await fetch(`/knowledge/api/documents/${documentId}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(updateData)
            });
            
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to save changes');
            }
            
            // Reload document to get updated data (including inherited tags)
            await DocumentViewer.loadDocument(documentId, modal);
            
            // Show brief success indicator
            DocumentViewer.showAutoSaveIndicator('Saved');
            
        } catch (error) {
            console.error('Error auto-saving:', error);
            DocumentViewer.showAutoSaveIndicator('Error saving', 'error');
        }
    }
    
    static showAutoSaveIndicator(message, type = 'success') {
        const modal = document.getElementById('documentViewerModal');
        if (!modal) return;
        
        // Remove existing indicator
        const existing = modal.querySelector('.auto-save-indicator');
        if (existing) existing.remove();
        
        // Create indicator
        const indicator = document.createElement('div');
        indicator.className = `auto-save-indicator auto-save-indicator-${type}`;
        indicator.textContent = message;
        modal.querySelector('.document-viewer-content').appendChild(indicator);
        
        // Auto-hide after 2 seconds
        setTimeout(() => {
            if (indicator.parentNode) {
                indicator.style.opacity = '0';
                setTimeout(() => indicator.remove(), 300);
            }
        }, 2000);
    }
    
    
    static showMessage(message, type) {
        const modal = document.getElementById('documentViewerModal');
        if (!modal) return;
        
        // Remove existing message
        const existingMsg = modal.querySelector('.viewer-message');
        if (existingMsg) existingMsg.remove();
        
        // Create message element
        const msgEl = document.createElement('div');
        msgEl.className = `viewer-message viewer-message-${type}`;
        msgEl.textContent = message;
        
        const viewerBody = modal.querySelector('#viewerBody');
        if (viewerBody) {
            viewerBody.insertBefore(msgEl, viewerBody.firstChild);
            
            // Auto-hide after 3 seconds
            setTimeout(() => {
                msgEl.remove();
            }, 3000);
        }
    }
    
    static close() {
        const modal = document.getElementById('documentViewerModal');
        if (modal) {
            modal.style.display = 'none';
            document.body.style.overflow = '';
            // Reset edit mode
            DocumentViewer.isEditMode = false;
            DocumentViewer.currentDocument = null;
        }
    }
    
    static async deleteDocument() {
        const modal = document.getElementById('documentViewerModal');
        if (!modal || !DocumentViewer.currentDocument) {
            return;
        }
        
        const documentId = DocumentViewer.currentDocument.document_id;
        const documentTitle = DocumentViewer.currentDocument.title || 'this document';
        
        // Confirm deletion
        if (!confirm(`Are you sure you want to delete "${documentTitle}"?\n\nThis action cannot be undone.`)) {
            return;
        }
        
        const deleteBtn = modal.querySelector('#deleteBtn');
        if (deleteBtn) {
            deleteBtn.disabled = true;
            deleteBtn.textContent = 'Deleting...';
        }
        
        try {
            const response = await fetch(`/knowledge/api/documents/${documentId}`, {
                method: 'DELETE'
            });
            
            if (!response.ok) {
                let errorMessage = 'Failed to delete document';
                try {
                    const error = await response.json();
                    errorMessage = error.error || errorMessage;
                } catch (e) {
                    errorMessage = response.statusText || errorMessage;
                }
                throw new Error(errorMessage);
            }
            
            // Close modal
            DocumentViewer.close();
            
            // Show success message in modal before closing
            DocumentViewer.showMessage('Document deleted successfully', 'success');
            
            // Close modal after a short delay
            setTimeout(() => {
                DocumentViewer.close();
                
                // Reload documents list if function exists
                if (typeof loadDocumentsList === 'function') {
                    loadDocumentsList();
                }
            }, 1500);
            
        } catch (error) {
            // Show error message in modal
            DocumentViewer.showMessage(error.message || 'Failed to delete document', 'error');
            
            if (deleteBtn) {
                deleteBtn.disabled = false;
                deleteBtn.textContent = 'Delete Document';
            }
        }
    }
    
    static escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    static formatFileSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }
    
    static getFileTypeLabel(mimeType) {
        const typeMap = {
            'application/pdf': 'PDF Document',
            'application/msword': 'Word Document',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'Word Document',
            'text/plain': 'Text File',
            'application/vnd.ms-excel': 'Excel Spreadsheet',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'Excel Spreadsheet'
        };
        return typeMap[mimeType] || mimeType;
    }
}

