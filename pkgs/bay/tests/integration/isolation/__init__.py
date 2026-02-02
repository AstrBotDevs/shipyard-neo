"""Container isolation tests package.

Covers:
- Verify code runs in container environment (test_container.py)
- Verify user/privilege isolation
- Verify filesystem isolation

Parallel-safe: Yes - each test creates/deletes its own sandbox.
"""
