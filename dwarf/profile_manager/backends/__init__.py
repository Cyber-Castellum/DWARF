from profile_manager.backends.base import Backend, BackendArtifacts, write_artifacts

_BACKENDS: dict[str, Backend] = {}


def register_backend(backend: Backend) -> None:
    _BACKENDS[backend.name] = backend


def _ensure_registered() -> None:
    """Register built-in backends lazily.

    Done lazily (not at import time) to avoid a circular import: antithesis.py
    imports backends.base, and importing any backends submodule runs this
    package __init__. Registering inside a function keeps both import orders safe.
    """
    if _BACKENDS:
        return
    from profile_manager.backends.local import LocalDevnetBackend
    from profile_manager.antithesis import AntithesisBackend

    register_backend(LocalDevnetBackend())
    register_backend(AntithesisBackend())


def get_backend(name: str) -> Backend:
    _ensure_registered()
    if name not in _BACKENDS:
        raise KeyError(f"Unknown backend: {name}")
    return _BACKENDS[name]


def backend_names() -> list[str]:
    _ensure_registered()
    return sorted(_BACKENDS)
