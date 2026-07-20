"""Supported narration languages.

Single source of truth for which languages the pipeline can produce and how to
name them in LLM prompts. Adding a language = add its code here and to the
`Literal` in app/api/schemas.py (Literal can't derive from a tuple), plus the
frontend LANGUAGES list. Optionally give it a voice in Settings.tts_voice_by_language.
"""

DEFAULT_LANGUAGE = "en"

# ISO 639-1 codes. Kept in sync with the Literal in app/api/schemas.py.
SUPPORTED_LANGUAGES = ("en", "vi")

# Human-readable names used to name the target language inside LLM prompts.
LANGUAGE_NAMES = {
    "en": "English",
    "vi": "Vietnamese",
}


def language_name(language: str) -> str:
    """Display name for prompt injection; falls back to the code itself."""
    return LANGUAGE_NAMES.get(language, language)
