"""Markers for tests/run.py."""


def needs_llm(fn):
    """The test calls the model through Ollama; skipped offline (CI) or when Ollama is not ready."""
    fn.needs_llm = True
    return fn
