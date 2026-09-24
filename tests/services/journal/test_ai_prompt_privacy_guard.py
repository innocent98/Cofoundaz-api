"""Static import guard test for ai_prompt.py privacy invariant.

This module enforces a critical privacy boundary: the journal AI-prompt grounding
must ONLY read operational signals (roadmap milestones + mission focus), never
diary content or emotional data. This test verifies that app/services/journal/ai_prompt.py
does not import or reference JournalEntry, MoodLog, or any journal-content models.

If this test fails, the prompt grounding has been polluted with personal diary data.
"""

import inspect

from app.services.journal import ai_prompt


class TestAIPromptPrivacyGuard:
    """Verify ai_prompt.py reads ONLY operational signals, never journal content."""

    def test_no_forbidden_imports_or_references(self) -> None:
        """Verify ai_prompt reads only operational signals, never journal content."""
        src = inspect.getsource(ai_prompt)

        # Strip the module docstring to avoid false positives from the privacy statement itself.
        # The docstring documents the constraint; actual code violations are in function bodies.
        src_lines = src.split("\n")
        in_module_docstring = False
        code_lines = []

        for line in src_lines:
            if not in_module_docstring and line.strip().startswith('"""'):
                in_module_docstring = True
                continue
            if in_module_docstring and line.strip().endswith('"""'):
                in_module_docstring = False
                continue
            if not in_module_docstring:
                code_lines.append(line)

        code_src = "\n".join(code_lines)

        # Check for imports of journal models.
        assert "from app.db.models.journal" not in code_src, (
            "PRIVACY VIOLATION: ai_prompt.py must not import from app.db.models.journal. "
            "The prompt grounding must read ONLY operational signals (roadmap/mission)."
        )

        # Check for explicit JournalEntry or MoodLog imports/usage.
        assert "import JournalEntry" not in code_src, (
            "PRIVACY VIOLATION: ai_prompt.py must not import JournalEntry. "
            "The prompt grounding must read ONLY operational signals (roadmap/mission)."
        )
        assert "import MoodLog" not in code_src, (
            "PRIVACY VIOLATION: ai_prompt.py must not import MoodLog. "
            "The prompt grounding must read ONLY operational signals (roadmap/mission)."
        )

        # Check for content_encrypted (a JournalEntry field reference).
        assert "content_encrypted" not in code_src, (
            "PRIVACY VIOLATION: ai_prompt.py must not reference content_encrypted. "
            "The prompt grounding must read ONLY operational signals (roadmap/mission), "
            "never diary entries or encrypted content."
        )
