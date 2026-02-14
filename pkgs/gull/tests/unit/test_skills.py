"""Unit tests for built-in skills scanning and frontmatter parsing in Gull.

Tests the _parse_frontmatter and _scan_built_in_skills functions
used by the /meta endpoint to expose built-in skill metadata.
"""

from pathlib import Path

import pytest

from app.main import _parse_frontmatter, _scan_built_in_skills


class TestParseFrontmatter:
    """Tests for YAML frontmatter extraction from SKILL.md files."""

    def test_basic_frontmatter(self):
        """Should extract name and description from standard frontmatter."""
        text = """---
name: browser-automation
description: Browser automation via agent-browser CLI
---

# Browser Automation
"""
        result = _parse_frontmatter(text)
        assert result["name"] == "browser-automation"
        assert result["description"] == "Browser automation via agent-browser CLI"

    def test_no_frontmatter(self):
        """Should return empty dict when no frontmatter present."""
        text = "# Just a heading\n\nSome content."
        result = _parse_frontmatter(text)
        assert result == {}

    def test_empty_string(self):
        """Should return empty dict for empty string."""
        result = _parse_frontmatter("")
        assert result == {}

    def test_quoted_values(self):
        """Should strip quotes from frontmatter values."""
        text = """---
name: 'quoted-skill'
description: "A quoted description"
---
"""
        result = _parse_frontmatter(text)
        assert result["name"] == "quoted-skill"
        assert result["description"] == "A quoted description"


class TestParseFrontmatterDirtyInputs:
    def test_crlf_line_endings(self):
        text = "---\r\nname: a\r\ndescription: b\r\n---\r\n# Title\r\n"
        result = _parse_frontmatter(text)
        assert result["name"] == "a"
        assert result["description"] == "b"

    def test_leading_blank_lines(self):
        text = "\n\n---\nname: a\ndescription: b\n---\n"
        result = _parse_frontmatter(text)
        assert result["name"] == "a"

    def test_utf8_bom(self):
        text = "\ufeff---\nname: a\ndescription: b\n---\n"
        result = _parse_frontmatter(text)
        assert result["name"] == "a"


class TestScanBuiltInSkills:
    """Tests for scanning /app/skills/ directories."""

    def test_scan_with_valid_skill(self, tmp_path: Path):
        """Should find and parse a valid skill directory."""
        skill_dir = tmp_path / "browser-automation"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: browser-automation\ndescription: Browser auto\n---\n"
        )

        result = _scan_built_in_skills(root=tmp_path)

        assert len(result) == 1
        assert result[0]["name"] == "browser-automation"
        assert result[0]["description"] == "Browser auto"

    def test_scan_empty_directory(self, tmp_path: Path):
        """Should return empty list for directory with no skills."""
        result = _scan_built_in_skills(root=tmp_path)
        assert result == []

    def test_scan_nonexistent_directory(self):
        """Should return empty list for nonexistent directory."""
        result = _scan_built_in_skills(root=Path("/nonexistent"))
        assert result == []

    def test_scan_with_references_subdir(self, tmp_path: Path):
        """Should scan skills that have references/ subdirectory."""
        skill_dir = tmp_path / "browser-automation"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: browser-automation\ndescription: Browser\n---\n"
        )
        refs_dir = skill_dir / "references"
        refs_dir.mkdir()
        (refs_dir / "browser.md").write_text("# Browser Deep Dive")

        result = _scan_built_in_skills(root=tmp_path)

        assert len(result) == 1
        assert result[0]["name"] == "browser-automation"

    def test_scan_skips_directories_without_skill_md(self, tmp_path: Path):
        """Should skip directories that don't contain SKILL.md."""
        valid = tmp_path / "valid-skill"
        valid.mkdir()
        (valid / "SKILL.md").write_text("---\nname: valid\ndescription: ok\n---\n")

        invalid = tmp_path / "not-a-skill"
        invalid.mkdir()
        (invalid / "README.md").write_text("not a skill")

        result = _scan_built_in_skills(root=tmp_path)

        assert len(result) == 1
        assert result[0]["name"] == "valid"
