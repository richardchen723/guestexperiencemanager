/**
 * Ticket Image Upload JavaScript
 * Handles image uploads for tickets and comments with drag-and-drop support
 */

class ImageUploader {
    constructor(container, options = {}) {
        this.container = typeof container === 'string' ? document.querySelector(container) : container;
        if (!this.container) {
            console.error('ImageUploader: Container element not found');
            return;
        }
        
        this.options = {
            uploadEndpoint: options.uploadEndpoint || '',
            deleteEndpoint: options.deleteEndpoint || '',
            listEndpoint: options.listEndpoint || '',
            imageServeUrl: options.imageServeUrl || '/tickets/api/images',
            thumbnailUrl: options.thumbnailUrl || '/tickets/api/images',
            onUploadComplete: options.onUploadComplete || (() => {}),
            onDeleteComplete: options.onDeleteComplete || (() => {}),
            maxFiles: options.maxFiles || 10,
            maxFileSize: options.maxFileSize || 2 * 1024 * 1024, // 2MB
            allowedTypes: options.allowedTypes || ['image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'image/gif', 'image/heic', 'image/heif']
        };
        
        this.images = [];
        this.uploadQueue = [];
        this.isUploading = false;
        
        this.render();
        // Load images after render completes (gallery needs to exist)
        setTimeout(() => {
            this.loadImages();
        }, 0);
    }
    
    render() {
        if (!this.container) return;
        
        this.container.innerHTML = '';
        this.container.className = 'ticket-images-container';
        
        // Header
        const header = document.createElement('div');
        header.className = 'ticket-images-header';
        header.innerHTML = '<h4>Images</h4>';
        this.container.appendChild(header);
        
        // Image gallery
        const gallery = document.createElement('div');
        gallery.className = 'ticket-images-gallery';
        gallery.id = `${this.container.id || 'imageGallery'}_gallery`;
        this.container.appendChild(gallery);
        this.gallery = gallery;
        
        // Upload area
        const uploadArea = document.createElement('div');
        uploadArea.className = 'ticket-images-upload';
        uploadArea.id = `${this.container.id || 'imageUpload'}_upload`;
        
        const uploadInput = document.createElement('input');
        uploadInput.type = 'file';
        uploadInput.accept = this.options.allowedTypes.join(',');
        uploadInput.multiple = true;
        uploadInput.style.display = 'none';
        uploadInput.addEventListener('change', (e) => this.handleFileSelect(e));
        this.uploadInput = uploadInput;
        
        const uploadLabel = document.createElement('label');
        uploadLabel.className = 'ticket-images-upload-label';
        uploadLabel.htmlFor = uploadInput.id || 'imageUploadInput';
        uploadLabel.innerHTML = `
            <div class="upload-icon">📷</div>
            <div class="upload-text">Click to upload or drag and drop</div>
            <div class="upload-hint">PNG, JPG, WebP, GIF, HEIC up to 2MB</div>
        `;
        uploadLabel.appendChild(uploadInput);
        
        uploadArea.appendChild(uploadLabel);
        this.container.appendChild(uploadArea);
        this.uploadArea = uploadArea;
        
        // Drag and drop handlers
        uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadArea.classList.add('drag-over');
        });
        
        uploadArea.addEventListener('dragleave', () => {
            uploadArea.classList.remove('drag-over');
        });
        
        uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('drag-over');
            const files = Array.from(e.dataTransfer.files);
            this.handleFiles(files);
        });
        
        // Click to upload
        uploadLabel.addEventListener('click', (e) => {
            if (e.target !== uploadInput) {
                e.preventDefault();
                uploadInput.click();
            }
        });
    }
    
    handleFileSelect(e) {
        const files = Array.from(e.target.files);
        this.handleFiles(files);
        // Reset input
        e.target.value = '';
    }
    
    handleFiles(files) {
        // Filter valid files (allow any size - backend will handle large files)
        const validFiles = files.filter(file => {
            if (!this.options.allowedTypes.includes(file.type)) {
                alert(`File ${file.name} is not a supported image type`);
                return false;
            }
            // Don't reject large files - backend will create thumbnail only
            return true;
        });
        
        if (validFiles.length === 0) return;
        
        // Check total file count
        if (this.images.length + validFiles.length > this.options.maxFiles) {
            alert(`Maximum ${this.options.maxFiles} images allowed`);
            return;
        }
        
        // Add to upload queue
        validFiles.forEach(file => {
            this.uploadQueue.push(file);
        });
        
        // Start uploading
        this.processUploadQueue();
    }
    
    async processUploadQueue() {
        if (this.isUploading || this.uploadQueue.length === 0) return;
        
        this.isUploading = true;
        this.updateUploadArea();
        
        while (this.uploadQueue.length > 0) {
            const file = this.uploadQueue.shift();
            await this.uploadFile(file);
        }
        
        this.isUploading = false;
        this.updateUploadArea();
    }
    
    async uploadFile(file) {
        const formData = new FormData();
        formData.append('image', file);
        
        // Show preview
        const preview = this.createPreviewElement(file);
        this.gallery.appendChild(preview);
        
        try {
            const response = await fetch(this.options.uploadEndpoint, {
                method: 'POST',
                body: formData
            });
            
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Upload failed');
            }
            
            const imageData = await response.json();
            this.images.push(imageData);
            
            // Replace preview with actual image
            preview.remove();
            this.renderImage(imageData);
            
            if (this.options.onUploadComplete) {
                this.options.onUploadComplete(imageData);
            }
        } catch (error) {
            console.error('Error uploading image:', error);
            preview.remove();
            alert(`Failed to upload ${file.name}: ${error.message}`);
        }
    }
    
    createPreviewElement(file) {
        const preview = document.createElement('div');
        preview.className = 'ticket-image-item image-preview';
        
        const img = document.createElement('img');
        img.src = URL.createObjectURL(file);
        img.className = 'ticket-image-thumb';
        
        const overlay = document.createElement('div');
        overlay.className = 'ticket-image-overlay';
        overlay.innerHTML = '<div class="upload-progress">Uploading...</div>';
        
        preview.appendChild(img);
        preview.appendChild(overlay);
        
        return preview;
    }
    
    renderImage(imageData) {
        const item = document.createElement('div');
        item.className = 'ticket-image-item';
        item.dataset.imageId = imageData.image_id;
        
        const img = document.createElement('img');
        img.src = `${this.options.thumbnailUrl}/${imageData.image_id}/thumbnail`;
        img.className = 'ticket-image-thumb';
        img.alt = imageData.file_name;
        img.loading = 'lazy';
        
        const overlay = document.createElement('div');
        overlay.className = 'ticket-image-overlay';
        
        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'ticket-image-delete';
        deleteBtn.innerHTML = '×';
        deleteBtn.title = 'Delete image';
        deleteBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.deleteImage(imageData.image_id);
        });
        
        overlay.appendChild(deleteBtn);
        
        // Click to view full size - add handler to item so it works even with overlay
        item.addEventListener('click', (e) => {
            // Don't open lightbox if clicking the delete button
            if (e.target === deleteBtn || deleteBtn.contains(e.target)) {
                return;
            }
            this.showLightbox(imageData);
        });
        
        item.appendChild(img);
        item.appendChild(overlay);
        this.gallery.appendChild(item);
    }
    
    async loadImages() {
        if (!this.options.listEndpoint) {
            console.warn('ImageUploader: No listEndpoint provided, skipping image load');
            return;
        }
        
        if (!this.gallery) {
            console.warn('ImageUploader: Gallery not initialized yet, retrying...');
            setTimeout(() => this.loadImages(), 100);
            return;
        }
        
        try {
            const response = await fetch(this.options.listEndpoint);
            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`Failed to load images: ${response.status} ${errorText}`);
            }
            
            const images = await response.json();
            this.images = images || [];
            
            console.log(`Loaded ${this.images.length} images for ticket`);
            
            // Render all images
            if (this.gallery) {
                this.gallery.innerHTML = '';
                if (this.images.length > 0) {
                    this.images.forEach(img => {
                        console.log('Rendering image:', img);
                        this.renderImage(img);
                    });
                } else {
                    // Show empty state
                    this.gallery.innerHTML = '<div class="tags-empty">No images uploaded yet</div>';
                }
            } else {
                console.error('Gallery element not found when trying to render images');
            }
        } catch (error) {
            console.error('Error loading images:', error);
            // Show error in gallery if it exists
            if (this.gallery) {
                this.gallery.innerHTML = `<div style="padding: 1rem; color: #ef4444; font-size: 0.875rem;">Error loading images: ${error.message}</div>`;
            }
        }
    }
    
    async deleteImage(imageId) {
        if (!confirm('Are you sure you want to delete this image?')) {
            return;
        }
        
        try {
            const response = await fetch(`${this.options.deleteEndpoint}/${imageId}`, {
                method: 'DELETE'
            });
            
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Delete failed');
            }
            
            // Remove from local array
            this.images = this.images.filter(img => img.image_id !== imageId);
            
            // Remove from DOM
            const item = this.gallery.querySelector(`[data-image-id="${imageId}"]`);
            if (item) {
                item.remove();
            }
            
            if (this.options.onDeleteComplete) {
                this.options.onDeleteComplete(imageId);
            }
        } catch (error) {
            console.error('Error deleting image:', error);
            alert(`Failed to delete image: ${error.message}`);
        }
    }
    
    showLightbox(imageData) {
        // Create lightbox modal
        const lightbox = document.createElement('div');
        lightbox.className = 'image-lightbox';
        lightbox.innerHTML = `
            <div class="lightbox-content">
                <button class="lightbox-close">×</button>
                <img src="${this.options.imageServeUrl}/${imageData.image_id}" alt="${imageData.file_name}">
                <div class="lightbox-info">
                    <div>${imageData.file_name}</div>
                    <div>${imageData.width} × ${imageData.height}px</div>
                </div>
            </div>
        `;
        
        document.body.appendChild(lightbox);
        
        // Close handlers
        const closeBtn = lightbox.querySelector('.lightbox-close');
        const close = () => {
            lightbox.remove();
        };
        
        closeBtn.addEventListener('click', close);
        lightbox.addEventListener('click', (e) => {
            if (e.target === lightbox) close();
        });
        
        // ESC key
        const escHandler = (e) => {
            if (e.key === 'Escape') {
                close();
                document.removeEventListener('keydown', escHandler);
            }
        };
        document.addEventListener('keydown', escHandler);
    }
    
    updateUploadArea() {
        if (this.isUploading) {
            this.uploadArea.classList.add('uploading');
        } else {
            this.uploadArea.classList.remove('uploading');
        }
    }
    
    getImages() {
        return this.images;
    }
}

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { ImageUploader };
}

