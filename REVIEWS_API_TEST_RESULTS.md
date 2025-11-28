# Hostaway API Reviews Endpoint - Test Results

## Test Date
November 28, 2025

## Question
Does `get_reviews` support sorting by guest check-in date?

## Answer: **YES** ✓

### Key Findings

1. **Sorting by Check-In Date (`arrivalDate`)**
   - ✅ **SUPPORTED**: `sortBy=arrivalDate` with `order=desc` works correctly
   - Verified: Reviews are actually sorted in descending order by arrival date
   - Field name: `arrivalDate` (guest check-in date)

2. **Sorting by Check-Out Date (`departureDate`)**
   - ✅ **SUPPORTED**: `sortBy=departureDate` with `order=desc` works correctly
   - Verified: Reviews are actually sorted in descending order by departure date
   - Field name: `departureDate` (guest check-out date)
   - Note: ASC order may have issues, but DESC works reliably

3. **Other Supported Sort Fields**
   - ❌ `sortBy=insertedOn` - **DOES NOT WORK** (API accepts but doesn't sort)
   - ❌ `sortBy=updatedOn` - **DOES NOT WORK** (API accepts but doesn't sort)
     - Tested: Both DESC and ASC return same unsorted results
     - Conclusion: Parameter is ignored by the API

4. **Date Filtering (for Incremental Sync)**
   - ❌ `updatedOn` parameter - **DOES NOT WORK** (API accepts but ignores it)
     - Tested: API accepts parameter but returns reviews with `updatedOn` BEFORE cutoff date
     - Conclusion: Parameter is ignored by the API
   - ❓ `insertedOn` parameter - Needs testing (likely same issue)
   - ❓ `updatedAfter` parameter - Needs testing (likely same issue)

### Review Data Structure

Reviews contain the following date-related fields:
- `arrivalDate`: Guest check-in date (2023-02-18 15:00:00 format)
- `departureDate`: Guest check-out date
- `insertedOn`: When review was inserted into Hostaway system
- `updatedOn`: When review was last updated
- `reservationId`: Links to reservation (which has check-in date)

### API Parameters Currently Supported

The current `get_reviews()` method supports:
- `listingId` / `listing_id`
- `reservationId` / `reservation_id`
- `limit`
- `offset`
- `status`

### API Parameters That Work (but not in current code)

**Sorting (works):**
- `sortBy=arrivalDate` - ✅ Works (sorts by check-in date)
- `sortBy=departureDate` - ✅ Works (sorts by checkout date)
- `order` - Sort order (`asc` or `desc`)

**Sorting (does NOT work):**
- `sortBy=updatedOn` - ❌ API accepts but ignores (doesn't sort)
- `sortBy=insertedOn` - ❌ API accepts but ignores (doesn't sort)

**Filtering (does NOT work):**
- `updatedOn` - ❌ API accepts but ignores (returns all reviews)
- `insertedOn` - ❌ Likely same issue (not tested)
- `updatedAfter` - ❌ Likely same issue (not tested)

### Recommendations

1. **For Sorting by Check-In Date:**
   ```python
   reviews = client.get_reviews(
       sortBy='arrivalDate',
       order='desc',
       limit=100
   )
   ```

2. **For Sorting by Check-Out Date:**
   ```python
   reviews = client.get_reviews(
       sortBy='departureDate',
       order='desc',
       limit=100
   )
   ```

2. **For Incremental Sync:**
   ⚠️ **NOT POSSIBLE** - `updatedOn` parameter does not work
   ```python
   # This does NOT work - API ignores the parameter
   cutoff_date = datetime(2024, 1, 1)
   reviews = client.get_reviews(
       updatedOn=cutoff_date.isoformat(),  # ❌ Ignored by API
       limit=100
   )
   ```
   
   **Alternative approach needed:**
   - Fetch all reviews and filter client-side by `updatedOn` or `insertedOn`
   - Or use `arrivalDate` sorting to process most recent check-ins first

3. **Update API Client:**
   - Add `sortBy` and `order` parameters to `get_reviews()` method
   - Only `sortBy=arrivalDate` works - use this for sorting
   - Note: `updatedOn`/`insertedOn` filtering and sorting do NOT work (API ignores them)
   - For incremental sync: Must filter client-side after fetching all reviews

### Performance Impact

- **Current**: Fetches ALL reviews every time (slow for incremental sync)
- **With API filtering**: ❌ NOT POSSIBLE - `updatedOn` parameter doesn't work
- **With client-side filtering**: Can filter by `updatedOn`/`insertedOn` after fetching (moderate improvement)
- **With sorting**: Can process reviews in order (e.g., newest check-ins first using `sortBy=arrivalDate`)

### Test Results Summary

**Sorting:**
- ✅ `sortBy=arrivalDate` - **WORKS** (verified sorted DESC correctly)
- ✅ `sortBy=departureDate` - **WORKS** (verified sorted DESC correctly)
  - Can sort by guest checkout date
  - DESC order works reliably (newest checkout first)
  - ASC order may have issues
- ❌ `sortBy=insertedOn` - **DOES NOT WORK** (API accepts but doesn't sort)
- ❌ `sortBy=updatedOn` - **DOES NOT WORK** (API accepts but doesn't sort)
  - Tested both DESC and ASC - both return unsorted results
  - Same reviews returned regardless of sort order

**Filtering:**
- ❌ `updatedOn` - **DOES NOT WORK** (API accepts but ignores - returns all reviews)
- ❓ `insertedOn` - Not tested (likely same issue)
- ❓ `updatedAfter` - Not tested (likely same issue)

