"""
Runtime compatibility shims. Import this module before faster-whisper is used.

PyAV >= 14 removed the `metadata_errors` keyword argument from `av.open()`, but
faster-whisper still passes it when decoding audio, which raises:
    TypeError: open() got an unexpected keyword argument 'metadata_errors'
We wrap `av.open` once to drop that argument. Safe to import multiple times.
"""

try:
    import av

    if not getattr(av.open, "_subforge_patched", False):
        _orig_av_open = av.open

        def _safe_av_open(*args, **kwargs):
            kwargs.pop("metadata_errors", None)
            return _orig_av_open(*args, **kwargs)

        _safe_av_open._subforge_patched = True  # type: ignore[attr-defined]
        av.open = _safe_av_open
except Exception:  # pragma: no cover - av not installed
    pass
