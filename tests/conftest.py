from __future__ import annotations

import pytest

from docpilot.config import Environment, Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env=Environment.TEST,
        log_json=False,
        log_level="WARNING",
    )
