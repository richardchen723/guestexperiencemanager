#!/usr/bin/env python3
"""
Knowledge base API routes.
"""

from flask import Blueprint, render_template, request, jsonify, send_file, current_app
from pathlib import Path
import os
import logging
from datetime import datetime

from dashboard.auth.decorators import approved_required, admin_required
from dashboard.auth.session import get_current_user
from dashboard.config import KNOWLEDGE_DOCUMENTS_DIR, MAX_DOCUMENT_SIZE
from database.models import get_session as get_main_session, Document, DocumentListing, DocumentTag, Listing, Tag
from dashboard.config import MAIN_DATABASE_PATH
from dashboard.knowledge.document_parser import parse_document
from dashboard.knowledge.document_storage import save_document, validate_document, get_document_path, delete_document
from dashboard.knowledge.search_indexer import index_document_content
from dashboard.knowledge.search_utils import format_search_results

logger = logging.getLogger(__name__)

knowledge_bp = Blueprint('knowledge', __name__, url_prefix='/knowledge')


@knowledge_bp.route('/')
@approved_required
def knowledge_page():
    """Knowledge base main page."""
    return render_template('knowledge/index.html', current_user=get_current_user())


@knowledge_bp.route('/api/documents', methods=['POST'])
@approved_required
def api_upload_document():
    """Upload a document."""
    current_user = get_current_user()
    if not current_user:
        return jsonify({'error': 'Authentication required'}), 401
    
    # Check if file was uploaded
    if 'document' not in request.files:
        return jsonify({'error': 'No document file provided'}), 400
    
    file = request.files['document']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Get and validate form data
    title = request.form.get('title', '').strip() or file.filename
    if len(title) > 500:
        return jsonify({'error': 'Document title is too long (max 500 characters)'}), 400
    
    listing_ids = request.form.getlist('listing_ids')  # Can be multiple
    tag_names = request.form.getlist('tag_names')  # Can be multiple
    is_admin_only = request.form.get('is_admin_only', 'false').lower() == 'true'
    
    # Only admins can set is_admin_only
    if is_admin_only and not current_user.is_admin():
        logger.warning(f"User {current_user.user_id} attempted to set is_admin_only without admin privileges")
        is_admin_only = False
    
    session = get_main_session(MAIN_DATABASE_PATH)
    
    try:
        
        # Validate file
        is_valid, error = validate_document(file)
        if not is_valid:
            return jsonify({'error': error}), 400
        
        # Create document record first to get document_id
        document = Document(
            title=title,
            file_name=file.filename,
            file_path='',  # Will be set after saving file
            file_size=0,  # Will be set after saving file
            mime_type=file.content_type or 'application/pdf',
            is_admin_only=is_admin_only,
            uploaded_by=current_user.user_id
        )
        session.add(document)
        session.flush()  # Get document_id without committing
        document_id = document.document_id
        
        # Save file to filesystem
        file_path, file_name, file_size, file_hash = save_document(
            file, KNOWLEDGE_DOCUMENTS_DIR, document_id
        )
        
        # Update document record with file info
        document.file_path = file_path
        document.file_name = file_name
        document.file_size = file_size
        document.file_hash = file_hash
        
        # Parse document content
        full_file_path = get_document_path(KNOWLEDGE_DOCUMENTS_DIR, file_path)
        try:
            parsed_data = parse_document(str(full_file_path), document.mime_type)
            document.content_text = parsed_data.get('text', '')
        except Exception as e:
            logger.error(f"Error parsing document {document_id}: {e}", exc_info=True)
            # Continue without content_text - document can still be uploaded
        
        # Associate with listings
        listing_tag_ids = set()
        if listing_ids:
            for listing_id_str in listing_ids:
                try:
                    listing_id = int(listing_id_str)
                    # Verify listing exists
                    listing = session.query(Listing).filter(Listing.listing_id == listing_id).first()
                    if listing:
                        doc_listing = DocumentListing(
                            document_id=document_id,
                            listing_id=listing_id
                        )
                        session.add(doc_listing)
                        
                        # Collect tags from listings for inheritance
                        from database.models import ListingTag
                        listing_tags = session.query(ListingTag).filter(
                            ListingTag.listing_id == listing_id
                        ).all()
                        for lt in listing_tags:
                            listing_tag_ids.add(lt.tag_id)
                except (ValueError, TypeError):
                    continue
        
        # Inherit tags from listings
        for tag_id in listing_tag_ids:
            doc_tag = DocumentTag(
                document_id=document_id,
                tag_id=tag_id,
                is_inherited=True
            )
            session.add(doc_tag)
        
        # Add user-selected tags
        if tag_names:
            for tag_name in tag_names:
                if not tag_name or not tag_name.strip():
                    continue
                
                # Sanitize tag name length
                tag_name = tag_name.strip()[:100]
                if not tag_name:
                    continue
                
                try:
                    normalized_name = Tag.normalize_name(tag_name)
                except ValueError as e:
                    logger.warning(f"Invalid tag name '{tag_name}': {e}")
                    continue
                
                # Get or create tag
                tag = session.query(Tag).filter(Tag.name == normalized_name).first()
                if not tag:
                    tag = Tag(name=normalized_name)
                    session.add(tag)
                    session.flush()
                
                # Check if already added (inherited or user-selected)
                existing = session.query(DocumentTag).filter(
                    DocumentTag.document_id == document_id,
                    DocumentTag.tag_id == tag.tag_id
                ).first()
                
                if not existing:
                    doc_tag = DocumentTag(
                        document_id=document_id,
                        tag_id=tag.tag_id,
                        is_inherited=False
                    )
                    session.add(doc_tag)
        
        session.commit()
        
        # Index content for search (after commit)
        if document.content_text:
            try:
                index_document_content(session, document_id, document.content_text)
            except Exception as e:
                logger.warning(f"Error indexing document {document_id}: {e}", exc_info=True)
        
        # Return document data
        return jsonify({
            'document_id': document_id,
            'title': document.title,
            'file_name': document.file_name,
            'file_size': document.file_size,
            'mime_type': document.mime_type,
            'is_admin_only': document.is_admin_only,
            'created_at': document.created_at.isoformat() if document.created_at else None
        }), 201
        
    except ValueError as e:
        session.rollback()
        logger.error(f"ValueError uploading document: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        session.rollback()
        logger.error(f"Error uploading document: {e}", exc_info=True)
        return jsonify({'error': 'Failed to upload document'}), 500
    finally:
        session.close()


@knowledge_bp.route('/api/documents', methods=['GET'])
@approved_required
def api_list_documents():
    """List documents with optional filters."""
    current_user = get_current_user()
    if not current_user:
        return jsonify({'error': 'Authentication required'}), 401
    
    # Support both singular listing_id (backward compatibility) and plural listing_ids
    listing_id = request.args.get('listing_id', type=int)
    listing_ids_param = request.args.get('listing_ids', '')
    tag_ids_param = request.args.get('tag_ids', '')
    search_query = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    
    session = get_main_session(MAIN_DATABASE_PATH)
    
    try:
        from sqlalchemy import and_, or_
        from sqlalchemy.orm import joinedload
        
        # Start query
        query = session.query(Document).options(
            joinedload(Document.listings),
            joinedload(Document.tags)
        )
        
        # Apply access control filter
        if not current_user.is_admin():
            query = query.filter(Document.is_admin_only == False)
        
        # Parse listing IDs - support both singular (backward compat) and plural
        listing_ids = []
        if listing_id:
            listing_ids = [listing_id]
        elif listing_ids_param:
            listing_ids = [int(lid) for lid in listing_ids_param.split(',') if lid.strip() and lid.strip().isdigit()]
        
        # Filter by listings (if any selected)
        if listing_ids:
            query = query.join(DocumentListing).filter(
                DocumentListing.listing_id.in_(listing_ids)
            ).distinct()
        
        # Parse tag IDs
        tag_ids = []
        if tag_ids_param:
            tag_ids = [int(tid) for tid in tag_ids_param.split(',') if tid.strip() and tid.strip().isdigit()]
        
        # Filter by tags (if any selected) - AND logic: must match both listings AND tags if both are provided
        if tag_ids:
            query = query.join(DocumentTag).filter(
                DocumentTag.tag_id.in_(tag_ids)
            ).distinct()
        
        # Text search (simple LIKE search for now, full-text search in separate endpoint)
        if search_query:
            search_pattern = f"%{search_query}%"
            query = query.filter(
                or_(
                    Document.title.ilike(search_pattern),
                    Document.content_text.ilike(search_pattern)
                )
            )
        
        # Order by created date (newest first)
        query = query.order_by(Document.created_at.desc())
        
        # Pagination
        total = query.count()
        documents = query.offset((page - 1) * per_page).limit(per_page).all()
        
        # Get listings and tags for each document
        listing_ids = set()
        tag_ids = set()
        for doc in documents:
            for dl in doc.listings:
                listing_ids.add(dl.listing_id)
            for dt in doc.tags:
                tag_ids.add(dt.tag_id)
        
        # Fetch listings and tags
        listings_map = {}
        if listing_ids:
            listings = session.query(Listing).filter(Listing.listing_id.in_(listing_ids)).all()
            listings_map = {l.listing_id: {
                'listing_id': l.listing_id,
                'name': l.name,
                'internal_listing_name': l.internal_listing_name
            } for l in listings}
        
        tags_map = {}
        if tag_ids:
            tags = session.query(Tag).filter(Tag.tag_id.in_(tag_ids)).all()
            tags_map = {t.tag_id: {
                'tag_id': t.tag_id,
                'name': t.name,
                'color': t.color
            } for t in tags}
        
        # Build response
        result = []
        for doc in documents:
            doc_dict = {
                'document_id': doc.document_id,
                'title': doc.title,
                'file_name': doc.file_name,
                'file_size': doc.file_size,
                'mime_type': doc.mime_type,
                'is_admin_only': doc.is_admin_only,
                'uploaded_by': doc.uploaded_by,
                'created_at': doc.created_at.isoformat() if doc.created_at else None,
                'listings': [listings_map[dl.listing_id] for dl in doc.listings if dl.listing_id in listings_map],
                'tags': [{
                    **tags_map[dt.tag_id],
                    'is_inherited': dt.is_inherited
                } for dt in doc.tags if dt.tag_id in tags_map]
            }
            result.append(doc_dict)
        
        return jsonify({
            'documents': result,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page
        })
        
    except Exception as e:
        logger.error(f"Error listing documents: {e}", exc_info=True)
        return jsonify({'error': 'Failed to list documents'}), 500
    finally:
        session.close()


@knowledge_bp.route('/api/documents/<int:document_id>', methods=['GET'])
@approved_required
def api_get_document(document_id):
    """Get document details."""
    current_user = get_current_user()
    if not current_user:
        return jsonify({'error': 'Authentication required'}), 401
    
    session = get_main_session(MAIN_DATABASE_PATH)
    
    try:
        from sqlalchemy.orm import joinedload
        
        document = session.query(Document).options(
            joinedload(Document.listings),
            joinedload(Document.tags)
        ).filter(Document.document_id == document_id).first()
        
        if not document:
            return jsonify({'error': 'Document not found'}), 404
        
        # Check access control
        if document.is_admin_only and not current_user.is_admin():
            return jsonify({'error': 'Access denied'}), 403
        
        # Get listings and tags
        listing_ids = [dl.listing_id for dl in document.listings]
        tag_ids = [dt.tag_id for dt in document.tags]
        
        listings = []
        if listing_ids:
            listings_query = session.query(Listing).filter(Listing.listing_id.in_(listing_ids)).all()
            listings = [{
                'listing_id': l.listing_id,
                'name': l.name,
                'internal_listing_name': l.internal_listing_name
            } for l in listings_query]
        
        tags = []
        if tag_ids:
            tags_query = session.query(Tag).filter(Tag.tag_id.in_(tag_ids)).all()
            tags = [{
                'tag_id': t.tag_id,
                'name': t.name,
                'color': t.color,
                'is_inherited': next((dt.is_inherited for dt in document.tags if dt.tag_id == t.tag_id), False)
            } for t in tags_query]
        
        return jsonify({
            'document_id': document.document_id,
            'title': document.title,
            'file_name': document.file_name,
            'file_size': document.file_size,
            'mime_type': document.mime_type,
            'is_admin_only': document.is_admin_only,
            'uploaded_by': document.uploaded_by,
            'created_at': document.created_at.isoformat() if document.created_at else None,
            'updated_at': document.updated_at.isoformat() if document.updated_at else None,
            'listings': listings,
            'tags': tags
        })
        
    except Exception as e:
        logger.error(f"Error getting document {document_id}: {e}", exc_info=True)
        return jsonify({'error': 'Failed to get document'}), 500
    finally:
        session.close()


@knowledge_bp.route('/api/documents/<int:document_id>/file', methods=['GET'])
@approved_required
def api_get_document_file(document_id):
    """Download/view document file."""
    current_user = get_current_user()
    if not current_user:
        return jsonify({'error': 'Authentication required'}), 401
    
    session = get_main_session(MAIN_DATABASE_PATH)
    
    try:
        document = session.query(Document).filter(Document.document_id == document_id).first()
        
        if not document:
            return jsonify({'error': 'Document not found'}), 404
        
        # Check access control
        if document.is_admin_only and not current_user.is_admin():
            return jsonify({'error': 'Access denied'}), 403
        
        # Get file path
        file_path = get_document_path(KNOWLEDGE_DOCUMENTS_DIR, document.file_path)
        
        if not file_path.exists():
            return jsonify({'error': 'File not found'}), 404
        
        # Determine if download or inline view
        download = request.args.get('download', 'false').lower() == 'true'
        
        return send_file(
            str(file_path),
            mimetype=document.mime_type,
            as_attachment=download,
            download_name=document.file_name
        )
        
    except Exception as e:
        logger.error(f"Error serving document file {document_id}: {e}", exc_info=True)
        return jsonify({'error': 'Failed to serve document'}), 500
    finally:
        session.close()


@knowledge_bp.route('/api/documents/search', methods=['POST'])
@approved_required
def api_search_documents():
    """Full-text search documents."""
    current_user = get_current_user()
    if not current_user:
        return jsonify({'error': 'Authentication required'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request data'}), 400
    
    query_text = data.get('query', '').strip()
    if not query_text:
        return jsonify({'error': 'Search query is required'}), 400
    
    # Support both singular listing_id (backward compatibility) and plural listing_ids
    listing_id = data.get('listing_id')
    listing_ids = data.get('listing_ids', [])
    tag_ids = data.get('tag_ids', [])
    limit = data.get('limit', 50)
    
    # Normalize listing_ids - support both singular and plural
    if listing_id:
        listing_ids = [listing_id] if not listing_ids else listing_ids
    elif not listing_ids:
        listing_ids = []
    
    # Ensure listing_ids and tag_ids are lists of integers
    if isinstance(listing_ids, str):
        listing_ids = [int(lid) for lid in listing_ids.split(',') if lid.strip() and lid.strip().isdigit()]
    elif not isinstance(listing_ids, list):
        listing_ids = []
    else:
        listing_ids = [int(lid) for lid in listing_ids if lid]
    
    if isinstance(tag_ids, str):
        tag_ids = [int(tid) for tid in tag_ids.split(',') if tid.strip() and tid.strip().isdigit()]
    elif not isinstance(tag_ids, list):
        tag_ids = []
    else:
        tag_ids = [int(tid) for tid in tag_ids if tid]
    
    session = get_main_session(MAIN_DATABASE_PATH)
    
    try:
        from sqlalchemy import text, func, and_, or_
        from sqlalchemy.orm import joinedload
        
        # Check if content_tsvector column exists
        check_column = session.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.columns 
                WHERE table_schema = 'public' 
                AND table_name = 'documents' 
                AND column_name = 'content_tsvector'
            )
        """))
        has_tsvector = check_column.scalar()
        
        if has_tsvector:
            # Use full-text search with tsvector
            sql_query = """
                SELECT d.document_id, ts_rank(d.content_tsvector, plainto_tsquery('english', :query)) as relevance
                FROM documents d
                WHERE d.content_tsvector @@ plainto_tsquery('english', :query)
            """
            
            params = {'query': query_text}
            
            # Add access control filter
            if not current_user.is_admin():
                sql_query += " AND d.is_admin_only = false"
            
            # Add listing filter (support multiple listings)
            if listing_ids:
                listing_ids_str = ','.join(str(lid) for lid in listing_ids)
                sql_query += f"""
                    AND EXISTS (
                        SELECT 1 FROM document_listings dl 
                        WHERE dl.document_id = d.document_id 
                        AND dl.listing_id IN ({listing_ids_str})
                    )
                """
            
            # Add tag filter
            if tag_ids:
                tag_ids_str = ','.join(str(tid) for tid in tag_ids)
                sql_query += f"""
                    AND EXISTS (
                        SELECT 1 FROM document_tags dt 
                        WHERE dt.document_id = d.document_id 
                        AND dt.tag_id IN ({tag_ids_str})
                    )
                """
            
            # Order by relevance
            sql_query += " ORDER BY relevance DESC LIMIT :limit"
            params['limit'] = limit
            
            # Execute query
            result = session.execute(text(sql_query), params)
            rows = result.fetchall()
        else:
            # Fallback to simple LIKE search if tsvector column doesn't exist
            from sqlalchemy import or_
            query = session.query(Document).filter(
                or_(
                    Document.title.ilike(f"%{query_text}%"),
                    Document.content_text.ilike(f"%{query_text}%")
                )
            )
            
            # Apply access control filter
            if not current_user.is_admin():
                query = query.filter(Document.is_admin_only == False)
            
            # Add listing filter (support multiple listings) - AND logic with tags
            if listing_ids:
                query = query.join(DocumentListing).filter(
                    DocumentListing.listing_id.in_(listing_ids)
                ).distinct()
            
            # Add tag filter - AND logic: must match both listings AND tags if both are provided
            if tag_ids:
                query = query.join(DocumentTag).filter(
                    DocumentTag.tag_id.in_(tag_ids)
                ).distinct()
            
            # Limit results
            documents = query.limit(limit).all()
            
            # Convert to format expected below
            rows = [(doc.document_id, 1.0) for doc in documents]  # Default relevance of 1.0
        
        # Get document IDs
        document_ids = [row[0] for row in rows]  # First column is document_id
        
        if not document_ids:
            return jsonify({'results': [], 'total': 0})
        
        # Fetch full document objects with relationships
        documents = session.query(Document).options(
            joinedload(Document.listings),
            joinedload(Document.tags)
        ).filter(Document.document_id.in_(document_ids)).all()
        
        # Create relevance map
        relevance_map = {row[0]: float(row[1]) for row in rows}  # document_id -> relevance
        
        # Get listings and tags
        listing_ids = set()
        tag_ids_set = set()
        for doc in documents:
            for dl in doc.listings:
                listing_ids.add(dl.listing_id)
            for dt in doc.tags:
                tag_ids_set.add(dt.tag_id)
        
        listings_map = {}
        if listing_ids:
            listings = session.query(Listing).filter(Listing.listing_id.in_(listing_ids)).all()
            listings_map = {l.listing_id: {
                'listing_id': l.listing_id,
                'name': l.name,
                'internal_listing_name': l.internal_listing_name
            } for l in listings}
        
        tags_map = {}
        if tag_ids_set:
            tags = session.query(Tag).filter(Tag.tag_id.in_(tag_ids_set)).all()
            tags_map = {t.tag_id: {
                'tag_id': t.tag_id,
                'name': t.name,
                'color': t.color
            } for t in tags}
        
        # Build results
        results = []
        for doc in documents:
            results.append({
                'document_id': doc.document_id,
                'title': doc.title,
                'content_text': doc.content_text or '',
                'relevance_score': relevance_map.get(doc.document_id, 0.0),
                'listings': [listings_map[dl.listing_id] for dl in doc.listings if dl.listing_id in listings_map],
                'tags': [{
                    **tags_map[dt.tag_id],
                    'is_inherited': dt.is_inherited
                } for dt in doc.tags if dt.tag_id in tags_map]
            })
        
        # Sort by relevance (maintain order from query)
        results.sort(key=lambda x: relevance_map.get(x['document_id'], 0.0), reverse=True)
        
        # Format results with snippets
        formatted_results = format_search_results(results, query_text)
        
        return jsonify({
            'results': formatted_results,
            'total': len(formatted_results)
        })
        
    except Exception as e:
        logger.error(f"Error searching documents: {e}", exc_info=True)
        return jsonify({'error': 'Failed to search documents'}), 500
    finally:
        session.close()


@knowledge_bp.route('/api/documents/<int:document_id>', methods=['PUT'])
@approved_required
def api_update_document(document_id):
    """Update document metadata."""
    current_user = get_current_user()
    if not current_user:
        return jsonify({'error': 'Authentication required'}), 401
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request data'}), 400
    
    session = get_main_session(MAIN_DATABASE_PATH)
    
    try:
        document = session.query(Document).filter(Document.document_id == document_id).first()
        
        if not document:
            return jsonify({'error': 'Document not found'}), 404
        
        # Check access control
        if document.is_admin_only and not current_user.is_admin():
            return jsonify({'error': 'Access denied'}), 403
        
        # Update fields
        if 'title' in data:
            title = data['title'].strip()
            if len(title) > 500:
                return jsonify({'error': 'Document title is too long (max 500 characters)'}), 400
            document.title = title
        
        # Only admins can update is_admin_only
        if 'is_admin_only' in data:
            if current_user.is_admin():
                document.is_admin_only = bool(data['is_admin_only'])
            else:
                logger.warning(f"User {current_user.user_id} attempted to update is_admin_only without admin privileges")
                return jsonify({'error': 'Permission denied'}), 403
        
        # Update listings
        if 'listing_ids' in data:
            # Remove existing associations
            session.query(DocumentListing).filter(
                DocumentListing.document_id == document_id
            ).delete()
            
            # Remove existing inherited tags (will be re-added from new listings)
            from sqlalchemy import and_
            session.query(DocumentTag).filter(
                and_(
                    DocumentTag.document_id == document_id,
                    DocumentTag.is_inherited == True
                )
            ).delete()
            
            # Add new associations and collect tags for inheritance
            listing_ids = data['listing_ids']
            listing_tag_ids = set()
            if isinstance(listing_ids, list):
                for lid in listing_ids:
                    listing = session.query(Listing).filter(Listing.listing_id == lid).first()
                    if listing:
                        doc_listing = DocumentListing(
                            document_id=document_id,
                            listing_id=lid
                        )
                        session.add(doc_listing)
                        
                        # Collect tags from listings for inheritance
                        from database.models import ListingTag
                        listing_tags = session.query(ListingTag).filter(
                            ListingTag.listing_id == lid
                        ).all()
                        for lt in listing_tags:
                            listing_tag_ids.add(lt.tag_id)
            
            # Re-inherit tags from new listings
            for tag_id in listing_tag_ids:
                # Check if tag already exists (from user-selected tags)
                existing = session.query(DocumentTag).filter(
                    DocumentTag.document_id == document_id,
                    DocumentTag.tag_id == tag_id
                ).first()
                
                if not existing:
                    doc_tag = DocumentTag(
                        document_id=document_id,
                        tag_id=tag_id,
                        is_inherited=True
                    )
                    session.add(doc_tag)
        
        # Update tags
        if 'tag_names' in data:
            # Remove non-inherited tags
            from sqlalchemy import and_
            session.query(DocumentTag).filter(
                and_(
                    DocumentTag.document_id == document_id,
                    DocumentTag.is_inherited == False
                )
            ).delete()
            
            # Add new tags
            tag_names = data['tag_names']
            if isinstance(tag_names, list):
                for tag_name in tag_names:
                    if not tag_name or not tag_name.strip():
                        continue
                    
                    # Sanitize tag name length
                    tag_name = tag_name.strip()[:100]
                    if not tag_name:
                        continue
                    
                    try:
                        normalized_name = Tag.normalize_name(tag_name)
                    except ValueError as e:
                        logger.warning(f"Invalid tag name '{tag_name}': {e}")
                        continue
                    
                    # Get or create tag
                    tag = session.query(Tag).filter(Tag.name == normalized_name).first()
                    if not tag:
                        tag = Tag(name=normalized_name)
                        session.add(tag)
                        session.flush()
                    
                    # Check if already exists
                    existing = session.query(DocumentTag).filter(
                        DocumentTag.document_id == document_id,
                        DocumentTag.tag_id == tag.tag_id
                    ).first()
                    
                    if not existing:
                        doc_tag = DocumentTag(
                            document_id=document_id,
                            tag_id=tag.tag_id,
                            is_inherited=False
                        )
                        session.add(doc_tag)
        
        document.updated_at = datetime.utcnow()
        
        session.commit()
        
        return jsonify({
            'document_id': document.document_id,
            'title': document.title,
            'is_admin_only': document.is_admin_only,
            'updated_at': document.updated_at.isoformat()
        })
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error updating document {document_id}: {e}", exc_info=True)
        return jsonify({'error': 'Failed to update document'}), 500
    finally:
        session.close()


@knowledge_bp.route('/api/documents/<int:document_id>', methods=['DELETE'])
@approved_required
def api_delete_document(document_id):
    """Delete document."""
    current_user = get_current_user()
    if not current_user:
        return jsonify({'error': 'Authentication required'}), 401
    
    session = get_main_session(MAIN_DATABASE_PATH)
    
    try:
        document = session.query(Document).filter(Document.document_id == document_id).first()
        
        if not document:
            return jsonify({'error': 'Document not found'}), 404
        
        # Check permissions (only admin or uploader can delete)
        if not current_user.is_admin() and document.uploaded_by != current_user.user_id:
            return jsonify({'error': 'Permission denied'}), 403
        
        # Delete file from filesystem
        try:
            delete_document(KNOWLEDGE_DOCUMENTS_DIR, document.file_path)
        except Exception as e:
            logger.warning(f"Error deleting document file {document.file_path}: {e}")
        
        # Delete database record (cascade will handle junction tables)
        session.delete(document)
        session.commit()
        
        return jsonify({'message': 'Document deleted successfully'})
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error deleting document {document_id}: {e}", exc_info=True)
        return jsonify({'error': 'Failed to delete document'}), 500
    finally:
        session.close()


def register_knowledge_routes(app):
    """Register knowledge base routes with Flask app."""
    app.register_blueprint(knowledge_bp)

