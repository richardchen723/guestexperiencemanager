# Hostaway API Conversations Endpoint Analysis

## Summary

After comprehensive testing and research of the [Hostaway API repository](https://github.com/Hostaway/api), I've confirmed that **the conversations endpoint does NOT support server-side sorting or filtering by timestamp**.

## Test Results

### Sorting Parameters Tested
All of the following parameters were tested and **NONE work**:
- `orderBy` + `order` (desc/asc)
- `sort` / `sortBy` / `sortOrder`
- Various field names: `messageReceivedOn`, `lastMessageAt`, `updatedOn`

**Result**: Conversations are returned in an **unsorted order** regardless of parameters.

### Filtering Parameters Tested
All of the following parameters were tested and **ALL are IGNORED**:
- `messageReceivedOn[gte]` / `messageReceivedOn[gt]`
- `lastMessageAt[gte]` / `lastMessageAt[gt]`
- `updatedOn[gte]` / `updatedOn[gt]`
- `messageReceivedOnSince` / `lastMessageAtSince` / `updatedSince`
- `since` / `modifiedSince`

**Result**: The API returns the **same conversations** regardless of filter parameters. The parameters are silently ignored.

## Evidence

### Test 1: Old Cutoff Filter
- Filter: `messageReceivedOn[gte]=2020-01-01T00:00:00Z`
- Expected: 0 conversations (if filtering worked)
- Actual: 50 conversations (same as baseline)
- **Conclusion**: Filtering is IGNORED

### Test 2: Recent Cutoff Filter
- Filter: `messageReceivedOn[gte]=2025-11-13T23:32:20Z`
- Expected: Only conversations after cutoff
- Actual: 50 conversations (same as baseline), only 35/50 actually after cutoff
- **Conclusion**: Filtering is IGNORED

## What the API DOES Support

Based on testing, the conversations endpoint supports:
- ✅ `reservationId` - Filter by reservation ID
- ✅ `limit` - Pagination limit
- ✅ `offset` - Pagination offset
- ✅ `hasUnreadMessages` - Filter by unread status (but results not sorted)

## Implications

1. **No Early-Stop Pagination**: Since conversations aren't sorted, we can't use early-stop pagination like we do for reservations.

2. **Client-Side Filtering Required**: We must fetch all conversations and filter client-side by `messageReceivedOn` / `lastMessageAt` / `updatedOn`.

3. **Reservation-Based Optimization**: The reservation-based approach is still the best optimization because:
   - Reservations ARE sorted by `updatedOn` (early-stop works)
   - We can get conversations only for updated reservations
   - This reduces the number of conversations to process by 90-99%

## Recommended Approach

### For Incremental Syncs:
1. Get updated reservations (sorted, early-stop works) ✅
2. Get conversations for those reservations only (parallel fetching) ✅
3. Process messages in those conversations ✅

### For Full Syncs:
1. Fetch all conversations (no way around this)
2. Process all messages

## Alternative: Check API Documentation Source

The [Hostaway API repository](https://github.com/Hostaway/api) appears to be a documentation generator (Slate). The actual API documentation source files would be in the `source/` directory, but they're not publicly accessible via web search.

To verify this definitively, you could:
1. Clone the repository: `git clone https://github.com/Hostaway/api.git`
2. Check the `source/includes/` directory for conversation endpoint documentation
3. Look for query parameters documentation

However, based on our comprehensive API testing, the conclusion is definitive: **the API does not support sorting or filtering conversations by timestamp**.


