"""Integration/E2E tests entry module (human-facing).

This file intentionally contains **no test cases**.

Why it exists:
- Acts as a stable *entry reference* for the integration suite.
- Avoids the old pattern of re-exporting test classes/functions (that breaks module-level
  `pytestmark` in the source modules and becomes brittle after refactors).

How to run:
- Run the whole suite (recommended):

    pytest pkgs/bay/tests/integration -n auto --dist loadgroup

- Two-phase run (conceptual; Phase1 parallel, Phase2 exclusive):

    pytest pkgs/bay/tests/integration -n auto --dist loadgroup -m "not serial"
    pytest pkgs/bay/tests/integration -n 1 -m "serial"

- Or run via this file as a script (convenience wrapper):

    python pkgs/bay/tests/integration/test_e2e_api.py -n auto --dist loadgroup

Notes:
- Serial grouping is centralized in `pkgs/bay/tests/integration/conftest.py`.
- GC tests must be exclusive and are grouped by `SERIAL_GROUPS["gc"]`.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """Run the integration suite.

    This is a convenience wrapper so developers can call:
        python pkgs/bay/tests/integration/test_e2e_api.py <pytest-args>

    Returns pytest exit code.
    """

    import pytest

    args = list(argv) if argv is not None else sys.argv[1:]

    # Run the entire integration test directory by default.
    suite_dir = Path(__file__).resolve().parent

    return pytest.main([str(suite_dir), *args])


if __name__ == "__main__":
    raise SystemExit(main())
