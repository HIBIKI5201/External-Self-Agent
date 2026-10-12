"""Markers for tests/run.py."""


def needs_voicevox(fn):
    """The test asks a running VOICEVOX engine (config voicevox_url); skipped offline or when it is not up."""
    fn.needs_voicevox = True
    return fn


def needs_llm(fn):
    """The test calls the model through Ollama; skipped offline (CI) or when Ollama is not ready."""
    fn.needs_llm = True
    return fn
