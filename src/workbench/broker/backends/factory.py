from __future__ import annotations

from ...errors import WorkbenchError


def build_backend(settings):
    if settings.backend == "local":
        from .local import LocalBackend
        return LocalBackend(settings)
    if settings.backend == "winsandbox":
        from .winsandbox import WinSandboxBackend
        return WinSandboxBackend(settings)
    raise WorkbenchError(f"알 수 없는 백엔드: {settings.backend} (local | winsandbox)", code="bad_config")
