#!/usr/bin/env python3
"""
Search result formatting utilities for knowledge base.
"""

import re
from typing import List, Dict, Optional

def format_search_results(results: List[Dict], query: str, context_chars: int = 200) -> List[Dict]:
    """
    Format search results with context snippets.
    
    Args:
        results: List of search result dictionaries from database
        query: Search query string
        context_chars: Number of characters to include before/after match
    
    Returns:
        [
            {
                'document_id': int,
                'title': str,
                'snippet': str,  # Context around match
                'match_position': int,  # Character position of match
                'relevance_score': float,
                'listings': [...],
                'tags': [...]
            },
            ...
        ]
    """
    formatted_results = []
    
    # Normalize query for matching (case-insensitive)
    query_lower = query.lower()
    query_words = query_lower.split()
    
    for result in results:
        content_text = result.get('content_text', '')
        if not content_text:
            # If no content text, use title as snippet
            formatted_results.append({
                'document_id': result.get('document_id'),
                'title': result.get('title', 'Untitled'),
                'snippet': result.get('title', 'Untitled'),
                'match_position': 0,
                'relevance_score': result.get('relevance_score', 0.0),
                'listings': result.get('listings', []),
                'tags': result.get('tags', [])
            })
            continue
        
        # Find first occurrence of any query word in content
        match_position = -1
        best_match_word = None
        
        for word in query_words:
            if len(word) < 3:  # Skip very short words
                continue
            pos = content_text.lower().find(word)
            if pos != -1 and (match_position == -1 or pos < match_position):
                match_position = pos
                best_match_word = word
        
        # If no match found, use beginning of text
        if match_position == -1:
            match_position = 0
            snippet_start = 0
        else:
            # Find snippet start (before match)
            snippet_start = max(0, match_position - context_chars)
        
        # Extract snippet
        snippet_end = min(len(content_text), match_position + len(best_match_word or query) + context_chars)
        snippet = content_text[snippet_start:snippet_end]
        
        # Add ellipsis if needed
        if snippet_start > 0:
            snippet = '...' + snippet
        if snippet_end < len(content_text):
            snippet = snippet + '...'
        
        # Highlight search terms in snippet
        highlighted_snippet = highlight_search_terms(snippet, query_words)
        
        formatted_results.append({
            'document_id': result.get('document_id'),
            'title': result.get('title', 'Untitled'),
            'snippet': highlighted_snippet,
            'match_position': match_position,
            'relevance_score': result.get('relevance_score', 0.0),
            'listings': result.get('listings', []),
            'tags': result.get('tags', [])
        })
    
    return formatted_results


def highlight_search_terms(text: str, query_words: List[str]) -> str:
    """
    Highlight search terms in text using HTML <mark> tags.
    
    Args:
        text: Text to highlight
        query_words: List of search words to highlight
    
    Returns:
        Text with highlighted terms wrapped in <mark> tags
    """
    if not query_words:
        return text
    
    # Create regex pattern for all query words (case-insensitive)
    # Only highlight words with 3+ characters
    significant_words = [w for w in query_words if len(w) >= 3]
    if not significant_words:
        return text
    
    # Escape special regex characters
    escaped_words = [re.escape(word) for word in significant_words]
    pattern = r'\b(' + '|'.join(escaped_words) + r')\b'
    
    # Replace with highlighted version
    highlighted = re.sub(
        pattern,
        r'<mark>\1</mark>',
        text,
        flags=re.IGNORECASE
    )
    
    return highlighted

