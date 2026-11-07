"""API key timestamp storage contract."""

from datetime import UTC, datetime

from app.models.api_key import ApiKey


def test_created_at_is_naive_utc() -> None:
    """Default timestamps fit PostgreSQL's TIMESTAMP WITHOUT TIME ZONE columns."""
    before = datetime.now(UTC).replace(tzinfo=None)

    key = ApiKey(id="test-key", key_hash="test-hash", key_prefix="test-prefix")

    after = datetime.now(UTC).replace(tzinfo=None)
    assert key.created_at.tzinfo is None
    assert before <= key.created_at <= after
