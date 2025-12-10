#!/usr/bin/env python3
"""
Image processing utilities for ticket and comment images.
Handles validation, optimization, and thumbnail generation.
"""

import os
import uuid
from pathlib import Path
from typing import Tuple, Optional, Dict
from werkzeug.datastructures import FileStorage
from PIL import Image
import logging

logger = logging.getLogger(__name__)

# Register HEIF opener for HEIC/HEIF support if available
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:
    HEIF_SUPPORT = False
    logger.debug("pillow-heif not installed - HEIC/HEIF support may be limited")

# Allowed image MIME types
ALLOWED_MIME_TYPES = {
    'image/jpeg': 'JPEG',
    'image/jpg': 'JPEG',
    'image/png': 'PNG',
    'image/webp': 'WebP',
    'image/gif': 'GIF',
    'image/heic': 'HEIC',
    'image/heif': 'HEIC'
}

# Allowed file extensions
ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.heic', '.heif'}

# Max file size: 2MB
MAX_FILE_SIZE = 2 * 1024 * 1024

# Image optimization settings
MAX_WIDTH = 1920
MAX_HEIGHT = 1920
JPEG_QUALITY = 85
THUMBNAIL_SIZE = 300


def validate_image(file: FileStorage) -> Tuple[bool, Optional[str]]:
    """
    Validate an uploaded image file.
    Note: Large files (>2MB) are accepted but will be saved as thumbnails only.
    
    Args:
        file: Werkzeug FileStorage object
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not file:
        return False, "No file provided"
    
    # Check file size (allow any size, but will create thumbnail only if >2MB)
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)  # Reset to beginning
    
    if file_size == 0:
        return False, "File is empty"
    
    # Check file extension
    filename = file.filename or ''
    ext = Path(filename).suffix.lower()
    
    # Check MIME type first (more reliable)
    mime_type = file.content_type or ''
    
    # Allow HEIC/HEIF files - they will be converted to JPEG
    is_heic = ext in {'.heic', '.heif'} or mime_type in {'image/heic', 'image/heif'}
    
    if not is_heic:
        # For non-HEIC files, check extension and MIME type
        if ext not in ALLOWED_EXTENSIONS:
            return False, f"File type not allowed. Allowed types: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        
        if mime_type and mime_type not in ALLOWED_MIME_TYPES:
            return False, f"MIME type not allowed. Allowed types: {', '.join(sorted(ALLOWED_MIME_TYPES.keys()))}"
    
    # Validate that it's actually an image by trying to open it
    # HEIF opener is already registered at module level if available
    # For HEIC files, PIL might not be able to open them directly from FileStorage
    # even with pillow-heif, so we'll be more lenient and let processing handle it
    if is_heic:
        # For HEIC files, skip strict validation - we'll validate during processing
        # This is because PIL might not be able to open HEIC from FileStorage directly
        file.seek(0)
        return True, None
    
    # For non-HEIC files, validate by trying to open
    try:
        file.seek(0)
        img = Image.open(file)
        img.verify()  # Verify it's a valid image
        file.seek(0)  # Reset after verify
    except Exception as e:
        return False, f"Invalid image file: {str(e)}"
    
    return True, None


def get_image_info(file_path: str) -> Dict[str, any]:
    """
    Get image dimensions and metadata.
    
    Args:
        file_path: Path to image file
        
    Returns:
        Dictionary with width, height, format, and size
    """
    try:
        with Image.open(file_path) as img:
            return {
                'width': img.width,
                'height': img.height,
                'format': img.format,
                'mode': img.mode
            }
    except Exception as e:
        logger.error(f"Error getting image info for {file_path}: {e}")
        return {'width': None, 'height': None, 'format': None, 'mode': None}


def optimize_image(file_path: str, max_width: int = MAX_WIDTH, max_height: int = MAX_HEIGHT, 
                   quality: int = JPEG_QUALITY) -> Tuple[str, int, int]:
    """
    Optimize an image by resizing if needed and compressing.
    HEIC/HEIF files are automatically converted to JPEG.
    
    Args:
        file_path: Path to source image file
        max_width: Maximum width in pixels
        max_height: Maximum height in pixels
        quality: JPEG quality (1-100)
        
    Returns:
        Tuple of (output_path, width, height)
    """
    try:
        # Ensure HEIF opener is registered if file is HEIC/HEIF
        file_ext = Path(file_path).suffix.lower()
        if file_ext in {'.heic', '.heif'}:
            try:
                from pillow_heif import register_heif_opener
                register_heif_opener()
            except ImportError:
                raise ValueError(f"HEIC/HEIF support requires pillow-heif package. Please install it with: pip install pillow-heif")
        
        with Image.open(file_path) as img:
            # Convert RGBA to RGB for JPEG
            if img.mode in ('RGBA', 'LA', 'P'):
                # Create white background
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            original_width, original_height = img.size
            
            # Resize if needed (maintain aspect ratio)
            if original_width > max_width or original_height > max_height:
                img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            
            # Generate output filename
            output_path = str(Path(file_path).with_suffix('.jpg'))
            
            # Save optimized image
            img.save(output_path, 'JPEG', quality=quality, optimize=True)
            
            width, height = img.size
            
            # Remove original if it's different from output
            if output_path != file_path and os.path.exists(file_path):
                os.remove(file_path)
            
            return output_path, width, height
            
    except Exception as e:
        logger.error(f"Error optimizing image {file_path}: {e}")
        raise


def create_thumbnail(file_path: str, size: int = THUMBNAIL_SIZE) -> str:
    """
    Create a thumbnail version of an image.
    HEIC/HEIF files are automatically converted to JPEG.
    
    Args:
        file_path: Path to source image file
        size: Thumbnail size (square, in pixels)
        
    Returns:
        Path to thumbnail file
    """
    try:
        # Ensure HEIF opener is registered if file is HEIC/HEIF
        file_ext = Path(file_path).suffix.lower()
        if file_ext in {'.heic', '.heif'}:
            try:
                from pillow_heif import register_heif_opener
                register_heif_opener()
            except ImportError:
                raise ValueError(f"HEIC/HEIF support requires pillow-heif package. Please install it with: pip install pillow-heif")
        
        with Image.open(file_path) as img:
            # Convert to RGB if needed
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Create thumbnail (maintain aspect ratio, then crop to square)
            img.thumbnail((size, size), Image.Resampling.LANCZOS)
            
            # Crop to square if needed
            width, height = img.size
            if width != height:
                # Center crop
                left = (width - min(width, height)) // 2
                top = (height - min(width, height)) // 2
                right = left + min(width, height)
                bottom = top + min(width, height)
                img = img.crop((left, top, right, bottom))
            
            # Generate thumbnail filename
            thumbnail_path = str(Path(file_path).with_name(f"{Path(file_path).stem}_thumb.jpg"))
            
            # Save thumbnail
            img.save(thumbnail_path, 'JPEG', quality=85, optimize=True)
            
            return thumbnail_path
            
    except Exception as e:
        logger.error(f"Error creating thumbnail for {file_path}: {e}")
        raise


def save_uploaded_image(file: FileStorage, base_dir: str, subfolder: str) -> Tuple[str, str, int, int, Optional[str]]:
    """
    Save an uploaded image file, optimize it, and create a thumbnail.
    If file exceeds 2MB, only the thumbnail is saved.
    
    Args:
        file: Werkzeug FileStorage object
        base_dir: Base directory for image storage
        subfolder: Subfolder name (e.g., 'tickets' or 'comments')
        
    Returns:
        Tuple of (file_path, file_name, width, height, thumbnail_path)
    """
    # Validate image
    is_valid, error = validate_image(file)
    if not is_valid:
        raise ValueError(error)
    
    # Check file size
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    
    # Create directory structure
    upload_dir = Path(base_dir) / 'images' / subfolder
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate unique filename
    # Keep original extension for HEIC/HEIF files - they'll be converted to JPEG during processing
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        file_ext = '.jpg'  # Default to JPEG for unknown extensions
    
    # For HEIC/HEIF, keep the original extension so PIL can identify it
    # The processing functions will convert it to JPEG
    unique_filename = f"{uuid.uuid4().hex}{file_ext}"
    temp_path = upload_dir / f"temp_{unique_filename}"
    
    # Save original file temporarily (with original extension for HEIC files)
    file.seek(0)
    file.save(str(temp_path))
    
    # If file exceeds 2MB, only create and save thumbnail
    if file_size > MAX_FILE_SIZE:
        try:
            # Create thumbnail directly from temp file
            # HEIF opener is already registered at module level if available
            thumbnail_path = create_thumbnail(str(temp_path))
            
            # Get thumbnail dimensions
            with Image.open(thumbnail_path) as thumb_img:
                thumb_width, thumb_height = thumb_img.size
            
            # Use thumbnail as the main image (copy it to the main filename)
            final_path = upload_dir / unique_filename
            import shutil
            shutil.copy2(thumbnail_path, final_path)
            
            # Get relative paths
            relative_path = f"images/{subfolder}/{final_path.name}"
            relative_thumbnail_path = relative_path  # Same file for oversized images
            
            # Clean up temp files
            if temp_path.exists():
                temp_path.unlink()
            if thumbnail_path != str(final_path) and Path(thumbnail_path).exists():
                Path(thumbnail_path).unlink()
            
            return (
                relative_path,
                file.filename or unique_filename,
                thumb_width,
                thumb_height,
                relative_thumbnail_path
            )
        except Exception as e:
            # Clean up on error
            if temp_path.exists():
                temp_path.unlink()
            raise ValueError(f"Error processing large image: {str(e)}")
    
    # Normal processing for files <= 2MB
    # Optimize image (HEIF opener is already registered at module level if available)
    optimized_path, width, height = optimize_image(str(temp_path))
    
    # Remove "temp_" prefix from optimized filename if present
    optimized_path_obj = Path(optimized_path)
    if optimized_path_obj.name.startswith('temp_'):
        # Rename to remove "temp_" prefix
        final_optimized_path = optimized_path_obj.parent / optimized_path_obj.name[5:]  # Remove "temp_" (5 chars)
        if optimized_path != str(final_optimized_path):
            import shutil
            shutil.move(str(optimized_path), str(final_optimized_path))
            optimized_path = str(final_optimized_path)
    
    # Create thumbnail
    thumbnail_path = create_thumbnail(optimized_path)
    
    # Remove "temp_" prefix from thumbnail filename if present
    thumbnail_path_obj = Path(thumbnail_path)
    if thumbnail_path_obj.name.startswith('temp_'):
        # Rename to remove "temp_" prefix
        final_thumbnail_path = thumbnail_path_obj.parent / thumbnail_path_obj.name[5:]  # Remove "temp_" (5 chars)
        if thumbnail_path != str(final_thumbnail_path):
            import shutil
            shutil.move(str(thumbnail_path), str(final_thumbnail_path))
            thumbnail_path = str(final_thumbnail_path)
    
    # Get relative paths for database storage
    relative_path = f"images/{subfolder}/{Path(optimized_path).name}"
    relative_thumbnail_path = f"images/{subfolder}/{Path(thumbnail_path).name}"
    
    # Clean up temp file if it's different from optimized
    if temp_path.exists() and str(temp_path) != optimized_path:
        temp_path.unlink()
    
    return (
        relative_path,
        file.filename or unique_filename,
        width,
        height,
        relative_thumbnail_path
    )

