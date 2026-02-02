"""Core API tests for Bay.

This package contains tests for fundamental Bay API operations:
- Authentication (test_auth.py)
- Sandbox lifecycle: create, stop, delete (test_sandbox_lifecycle.py)
- Concurrent operations (test_concurrent.py)
- Idempotency (test_idempotency.py)
- TTL extension (test_extend_ttl.py)

All tests in this package are designed for parallel execution with pytest-xdist,
except for TTL-expiration tests which require serial execution due to timing sensitivity.
"""
