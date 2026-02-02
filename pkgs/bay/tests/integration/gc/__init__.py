"""GC integration tests.

All tests under this package are treated as *serial/exclusive* via
[`SERIAL_GROUPS["gc"]`](pkgs/bay/tests/integration/conftest.py:62).

Rationale:
- GC tests may create/delete global resources (sandboxes/volumes/containers).
- They must not overlap with other integration tests.
"""
