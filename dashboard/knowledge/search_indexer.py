#!/usr/bin/env python3
"""
Full-text search indexing utilities for documents.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


def index_document_content(session, document_id: int, content_text: str):
    """
    Create/update full-text search index for document.
    Uses PostgreSQL tsvector column with GIN index.
    
    Args:
        session: SQLAlchemy session
        document_id: Document ID
        content_text: Extracted text content from document
    """
    if not content_text:
        logger.warning(f"No content text provided for document {document_id}, skipping indexing")
        return
    
    try:
        # Update the tsvector column using PostgreSQL's to_tsvector function
        # The trigger should handle this automatically, but we can also do it manually
        # to ensure it's indexed immediately
        session.execute(
            text("""
                UPDATE documents 
                SET content_tsvector = to_tsvector('english', :content_text)
                WHERE document_id = :document_id
            """),
            {
                'content_text': content_text,
                'document_id': document_id
            }
        )
        session.commit()
        logger.debug(f"Indexed document {document_id} content for full-text search")
    except Exception as e:
        session.rollback()
        logger.error(f"Error indexing document {document_id}: {e}", exc_info=True)
        # Don't raise - indexing failure shouldn't prevent document upload


def rebuild_search_index(session):
    """
    Rebuild full-text search index for all documents.
    Useful for maintenance or after schema changes.
    
    Args:
        session: SQLAlchemy session
    """
    try:
        session.execute(text("""
            UPDATE documents 
            SET content_tsvector = to_tsvector('english', COALESCE(content_text, ''))
            WHERE content_text IS NOT NULL
        """))
        session.commit()
        logger.info("Rebuilt full-text search index for all documents")
    except Exception as e:
        session.rollback()
        logger.error(f"Error rebuilding search index: {e}", exc_info=True)
        raise

