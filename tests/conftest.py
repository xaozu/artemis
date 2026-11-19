# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Repository-wide pytest fixtures and classification helpers.

The default test paths contain only deterministic tests.  Tests under the
integration and end-to-end trees remain directly runnable, and receive stable
markers here so callers can select them without relying on filename patterns.
"""

from pathlib import Path

import pytest
from pydantic import SecretStr

from artemis.config.settings import settings
from artemis.drivers.mock.mock_driver import MockDeviceDriver

# 需要 API Key 的 LLM 服务商对应的 settings 字段（vertexai 走 ADC，不在此列）。
_LLM_API_KEY_FIELDS = (
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPEN_ROUTER_API_KEY",
    "XAI_API_KEY",
)


@pytest.fixture
def mock_driver():
    """Provide an isolated mock mobile driver."""
    return MockDeviceDriver(device_id="fixture-mock-device", width=1080, height=2400)


@pytest.fixture
def fake_llm_api_keys(monkeypatch):
    """为所有基于 API Key 的服务商注入占位凭据。

    ``AgentConfigBuilder().build()`` 会读取仓库 ``config/artemis.jsonc`` 并调用
    ``validate_providers()`` 校验凭据；不注入时测试结果取决于本机 ``.env``
    里配了哪家的 Key。占位值只用于通过存在性校验，测试不会真正发起请求。
    """
    for field in _LLM_API_KEY_FIELDS:
        monkeypatch.setattr(settings, field, SecretStr(f"test-{field.lower()}"))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Attach test-layer markers according to the owning test directory."""
    for item in items:
        parts = Path(str(item.path)).parts
        if "integration" in parts:
            item.add_marker(pytest.mark.integration)
        if "e2e" in parts:
            item.add_marker(pytest.mark.e2e)
