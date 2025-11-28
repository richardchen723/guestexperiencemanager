# Idempotency Review - Sync Processes

## Overview
This document reviews all sync processes to ensure they are idempotent (safe to run multiple times without creating duplicates or corrupting data).

## Issues Found

### 1. Message Sync - Potential Duplicate Key Issue ⚠️
**Location**: `sync/sync_messages.py`

**Issue**: 
- Uses `(conversation_id, created_at)` tuple for deduplication
- Database model has `message_id` as primary key (auto-increment)
- If API provides `id` field, we should use it instead
- Two messages in same conversation with same timestamp could conflict

**Current Code**:
```python
message_key = (conversation_id, created_at)
if message_key in existing_message_set:
    continue
```

**Recommendation**: 
- Check if API provides `id` field for messages
- If yes, use `message_id` from API as primary key
- If no, keep current approach but add additional uniqueness check

### 2. Listing Photos - Delete/Recreate Pattern ⚠️
**Location**: `sync/sync_listings.py:sync_listing_photos()`

**Issue**:
- Deletes all photos before recreating
- If sync fails after delete but before recreate, photos are lost
- Not truly idempotent if multiple syncs run simultaneously

**Current Code**:
```python
session.query(ListingPhoto).filter(
    ListingPhoto.listing_id == listing_id
).delete()
# Then recreates all photos
```

**Recommendation**:
- Use upsert pattern: check if photo exists by URL, update if exists, create if not
- Only delete photos that are no longer in API response
- This makes it truly idempotent

### 3. Guest Lookup - Email Not Unique ⚠️
**Location**: `sync/sync_reservations.py:get_or_create_guest()`

**Issue**:
- Uses email as lookup key, but email is not unique in database
- Multiple guests could have same email
- Could match wrong guest

**Current Code**:
```python
if not guest and guest_email and guest_email.lower() in guest_lookup['by_email']:
    guest = guest_lookup['by_email'][guest_email.lower()]
```

**Recommendation**:
- Primary lookup should be by `guest_external_account_id` (which is unique)
- Email lookup should be secondary/fallback only
- Current code already does this correctly (external_id first, then email)
- **Status**: Already correct, but document the behavior

### 4. Batch Commit Error Handling ✅
**Location**: All sync functions

**Status**: 
- Reviews sync has excellent error handling with rollback cleanup
- Other syncs have basic error handling
- Need to ensure all syncs clean up in-memory maps on rollback

**Recommendation**:
- Standardize error handling across all syncs
- Always clean up in-memory lookup maps on rollback
- Re-query existing records after rollback to refresh lookups

## Idempotency Verification by Sync Type

### ✅ Listings Sync
- **Primary Key**: `listing_id` (from API)
- **Deduplication**: Uses `existing_listing_map` by `listing_id`
- **Status**: **IDEMPOTENT** ✅
- **Notes**: Correctly checks for existing listings before creating

### ✅ Reservations Sync  
- **Primary Key**: `reservation_id` (from API)
- **Deduplication**: Uses `existing_reservation_map` by `reservation_id`
- **Status**: **IDEMPOTENT** ✅
- **Notes**: 
  - Correctly checks for existing reservations
  - Updates existing records instead of creating duplicates
  - Guest lookup is safe (external_id first, then email)

### ⚠️ Messages Sync
- **Primary Key**: `message_id` (auto-increment, but not used for dedup)
- **Deduplication**: Uses `(conversation_id, created_at)` tuple
- **Status**: **MOSTLY IDEMPOTENT** ⚠️
- **Issues**:
  - Should check if API provides `message_id` and use it
  - Current approach works but not ideal
- **Recommendation**: Verify API provides message ID, use it if available

### ✅ Reviews Sync
- **Primary Key**: `review_id` (from API)
- **Deduplication**: Uses `existing_review_map` by `review_id`
- **Status**: **IDEMPOTENT** ✅
- **Notes**: 
  - Excellent error handling with rollback cleanup
  - Properly tracks batch operations
  - Removes from lookup map on rollback

### ⚠️ Listing Photos Sync
- **Primary Key**: `photo_id` (auto-increment)
- **Deduplication**: None - deletes all and recreates
- **Status**: **NOT FULLY IDEMPOTENT** ⚠️
- **Issues**:
  - Delete/recreate pattern loses data if sync fails
  - No deduplication check
- **Recommendation**: Implement upsert pattern

## Fixes Applied ✅

### 1. Message Sync - Enhanced Deduplication ✅
**Fixed**: 
- Now checks for `message_id` from API if available
- Falls back to `(conversation_id, created_at)` tuple if no message_id
- Tracks both `message_id` and tuple in memory for faster lookups
- Re-queries existing messages after rollback to refresh lookups

**Code Changes**:
- Added `existing_message_ids` set for message_id-based lookups
- Enhanced deduplication to prefer message_id when available
- Improved rollback handling to refresh lookup sets

### 2. Listing Photos - Upsert Pattern ✅
**Fixed**:
- Changed from delete/recreate to upsert pattern
- Updates existing photos by URL, creates new ones
- Only deletes photos no longer in API response
- Truly idempotent - safe to run multiple times

**Code Changes**:
- Pre-loads existing photos by URL
- Updates existing photos instead of deleting
- Creates new photos only if URL doesn't exist
- Deletes only photos not in API response

## Recommendations

### High Priority
1. ✅ **Fix Listing Photos Sync**: Implement upsert pattern instead of delete/recreate - **DONE**
2. ✅ **Verify Message ID**: Check if API provides message ID, use it for deduplication - **DONE**

### Medium Priority
3. **Standardize Error Handling**: Ensure all syncs clean up in-memory maps on rollback
4. **Add Database Constraints**: Consider adding unique constraints where appropriate

### Low Priority
5. **Document Guest Lookup**: Document that email lookup is fallback only
6. **Add Integration Tests**: Test idempotency by running syncs multiple times

## Testing Recommendations

1. **Run each sync twice in a row** - should produce same results
2. **Run syncs with partial failures** - should not create partial duplicates
3. **Run multiple syncs simultaneously** - should handle race conditions gracefully
4. **Verify no duplicate records** - check database for duplicates after syncs

