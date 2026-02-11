"""Unit tests for built-in skills scanning and frontmatter parsing.

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
name: python-sandbox
description: Python execution environment guide
---

# Python Sandbox

Some content here.
"""
        result = _parse_frontmatter(text)
        assert result["name"] == "python-sandbox"
        assert result["description"] == "Python execution environment guide"

    def test_quoted_values(self):
        """Should strip quotes from frontmatter values."""
        text = """---
name: 'my-skill'
description: "A skill with quotes"
---

Content.
"""
        result = _parse_frontmatter(text)
        assert result["name"] == "my-skill"
        assert result["description"] == "A skill with quotes"

    def test_no_frontmatter(self):
        """Should return empty dict when no frontmatter present."""
        text = "# Just a heading\n\nSome content."
        result = _parse_frontmatter(text)
        assert result == {}

    def test_empty_string(self):
        """Should return empty dict for empty string."""
        result = _parse_frontmatter("")
        assert result == {}

    def test_frontmatter_with_extra_fields(self):
        """Should capture additional fields beyond name and description."""
        text = """---
name: test-skill
description: A test skill
version: 1.0
author: Team
---

Content.
"""
        result = _parse_frontmatter(text)
        assert result["name"] == "test-skill"
        assert result["version"] == "1.0"
        assert result["author"] == "Team"

    def test_multiline_description_takes_first_line(self):
        """Should handle description that spans one line in frontmatter."""
        text = """---
name: skill
description: Short and simple
---
"""
        result = _parse_frontmatter(text)
        assert result["description"] == "Short and simple"


class TestScanBuiltInSkills:
    """Tests for scanning /app/skills/ directories."""

    def test_scan_with_valid_skill(self, tmp_path: Path):
        """Should find and parse a valid skill directory."""
        skill_dir = tmp_path / "python-sandbox"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            """---
name: python-sandbox
description: Python execution environment
---

# Python Sandbox
"""
        )

        result = _scan_built_in_skills(root=tmp_path)

        assert len(result) == 1
        assert result[0]["name"] == "python-sandbox"
        assert result[0]["description"] == "Python execution environment"
        assert "SKILL.md" in result[0]["path"]

    def test_scan_multiple_skills(self, tmp_path: Path):
        """Should find all skill directories sorted by name."""
        for name in ["browser-automation", "python-sandbox"]:
            skill_dir = tmp_path / name
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {name} desc\n---\n"
            )

        result = _scan_built_in_skills(root=tmp_path)

        assert len(result) == 2
        assert result[0]["name"] == "browser-automation"
        assert result[1]["name"] == "python-sandbox"

    def test_scan_empty_directory(self, tmp_path: Path):
        """Should return empty list for directory with no skills."""
        result = _scan_built_in_skills(root=tmp_path)
        assert result == []

    def test_scan_nonexistent_directory(self):
        """Should return empty list for nonexistent directory."""
        result = _scan_built_in_skills(root=Path("/nonexistent"))
        assert result == []

    def test_scan_skips_directories_without_skill_md(self, tmp_path: Path):
        """Should skip directories that don't contain SKILL.md."""
        # Valid skill
        valid = tmp_path / "valid-skill"
        valid.mkdir()
        (valid / "SKILL.md").write_text("---\nname: valid\ndescription: ok\n---\n")

        # Invalid (no SKILL.md)
        invalid = tmp_path / "not-a-skill"
        invalid.mkdir()
        (invalid / "README.md").write_text("not a skill")

        result = _scan_built_in_skills(root=tmp_path)

        assert len(result) == 1
        assert result[0]["name"] == "valid"

    def test_scan_uses_dirname_as_fallback_name(self, tmp_path: Path):
        """Should use directory name when frontmatter has no 'name' field."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\ndescription: no name field\n---\n"
        )

        result = _scan_built_in_skills(root=tmp_path)

        assert len(result) == 1
        assert result[0]["name"] == "my-skill"

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

    def test_scan_handles_malformed_frontmatter(self, tmp_path: Path):
        """Should handle SKILL.md with malformed frontmatter gracefully."""
        skill_dir = tmp_path / "broken-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# No frontmatter at all")

        result = _scan_built_in_skills(root=tmp_path)

        assert len(result) == 1
        # Falls back to directory name, empty description
        assert result[0]["name"] == "broken-skill"
        assert result[0]["description"] == ""
