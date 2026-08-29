from datetime import date
from unittest.mock import patch

from flask import Flask

from dashboard.reviews import routes
from dashboard.reviews.query import (
    get_published_reviews,
    published_review_date_range,
    published_review_rating_bucket,
)
from database.models import Listing, ListingTag, Reservation, Review, Tag


class PublishedReviewQuery:
    def __init__(self, reviews):
        self.reviews = reviews

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def options(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self.reviews


class PublishedReviewSession:
    def __init__(self, reviews):
        self.reviews = reviews
        self.closed = False

    def query(self, *args, **kwargs):
        return PublishedReviewQuery(self.reviews)

    def close(self):
        self.closed = True


def make_published_review(
    review_id,
    listing_id,
    listing_name,
    review_date,
    overall_rating,
    guest_name,
    portfolio_tag=None,
):
    listing = Listing(listing_id=listing_id, name=listing_name, status='active')
    if portfolio_tag:
        tag = Tag(tag_id=review_id, name=portfolio_tag)
        listing.tags = [ListingTag(listing_id=listing_id, tag_id=review_id, tag=tag)]
    reservation = Reservation(
        reservation_id=review_id + 100,
        listing_id=listing_id,
        guest_name=guest_name,
        channel_name='airbnbOfficial',
        departure_date=review_date,
    )
    return Review(
        review_id=review_id,
        listing_id=listing_id,
        reservation_id=reservation.reservation_id,
        listing=listing,
        reservation=reservation,
        overall_rating=overall_rating,
        review_date=review_date,
        reviewer_name=guest_name,
        review_text=f'Review {review_id}',
        status='published',
        origin='Guest',
    )


def test_published_review_default_range_is_inclusive_90_days():
    assert published_review_date_range(today=date(2026, 8, 29)) == (
        date(2026, 6, 1),
        date(2026, 8, 29),
        False,
    )


def test_published_review_custom_range_validation():
    assert published_review_date_range(
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 30),
    ) == (date(2026, 4, 1), date(2026, 4, 30), True)

    try:
        published_review_date_range(
            start_date=date(2026, 5, 2),
            end_date=date(2026, 5, 1),
        )
        raise AssertionError('Expected a reversed range to fail')
    except ValueError as error:
        assert str(error) == 'To date cannot be earlier than From date'


def test_published_review_rating_uses_nearest_whole_star_band():
    assert published_review_rating_bucket(5.0) == 5
    assert published_review_rating_bucket(4.5) == 5
    assert published_review_rating_bucket(4.4) == 4
    assert published_review_rating_bucket(1.0) == 1
    assert published_review_rating_bucket(None) is None


@patch('dashboard.reviews.query.get_database_path', return_value='unused')
@patch('dashboard.reviews.query.get_session')
def test_published_review_filters_compose_and_summary_matches_results(get_session, _database_path):
    reviews = [
        make_published_review(1, 1001, 'Urban Five', date(2026, 8, 5), 10, 'Alex', 'Urban Stays'),
        make_published_review(2, 1002, 'Urban Four', date(2026, 8, 3), 7, 'Bailey', 'Urban Stays'),
        make_published_review(3, 558675, 'Crestwood Two', date(2026, 8, 4), 4, 'Casey'),
    ]
    session = PublishedReviewSession(reviews)
    get_session.return_value = session

    payload = get_published_reviews(
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 10),
        portfolio='Urban Stays',
        ratings=[4, 5],
        sort='rating_asc',
    )

    assert [review['review_id'] for review in payload['reviews']] == [2, 1]
    assert payload['summary'] == {
        'total': 2,
        'average_rating': 4.25,
        'five_star_count': 1,
        'portfolio_count': 1,
    }
    assert payload['filter_options']['range_total'] == 3
    assert payload['filters'] == {
        'portfolio': 'Urban Stays',
        'ratings': [4, 5],
        'sort': 'rating_asc',
    }
    rating_counts = {
        item['rating']: item['count']
        for item in payload['filter_options']['ratings']
    }
    assert rating_counts == {5: 1, 4: 1, 3: 0, 2: 1, 1: 0}
    assert session.closed


def test_published_review_api_parses_combined_filters_and_rejects_bad_ratings():
    app = Flask(__name__)
    payload = {'reviews': [], 'summary': {}, 'range': {}, 'filters': {}, 'filter_options': {}}
    with (
        patch.object(routes, 'get_published_reviews', return_value=payload) as get_reviews,
        app.test_request_context(
            '/reviews/api/published?start_date=2026-08-01&end_date=2026-08-10'
            '&portfolio=Urban%20Stays&ratings=5,3&sort=rating_desc'
        ),
    ):
        response, status = routes.api_published_reviews.__wrapped__()
        assert status == 200
        assert response.get_json() == payload
        get_reviews.assert_called_once_with(
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 10),
            portfolio='Urban Stays',
            ratings=[5, 3],
            sort='rating_desc',
        )

    with app.test_request_context('/reviews/api/published?ratings=five'):
        response, status = routes.api_published_reviews.__wrapped__()
        assert status == 400
        assert response.get_json() == {
            'error': 'Ratings must be comma-separated whole numbers from 1 to 5'
        }
