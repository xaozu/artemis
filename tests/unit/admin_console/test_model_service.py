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

import json
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from apps.admin_console.services.model_service import ModelService


@pytest.fixture
def google_operator_llm_config(monkeypatch):
    """注入明确的 LLM 配置（operator 为 google），并清空 ModelService 的 TTL 缓存。

    ``ModelService`` 默认读取仓库 ``config/artemis.jsonc``，provider 会随本仓库的
    默认配置变化；这里固定输入，只验证 profile 与架构名的映射以及 provider/model
    是否原样透传。
    """
    llm_cfg = SimpleNamespace(
        operator=SimpleNamespace(provider="google", model="gemini-test-flash"),
        default=None,
    )
    monkeypatch.setattr("artemis.config.parse_llm_config", lambda: llm_cfg)
    monkeypatch.setattr(ModelService, "_llm_info_cache", None)
    return llm_cfg


def test_get_active_model_info_pro_architecture(google_operator_llm_config):
    """Verify that pro profile returns Pro architecture while keeping real LLM model."""
    info = ModelService.get_active_model_info("pro")
    assert info["name"] == "Pro"
    assert info["architecture"] == "ARTEMIS Pro"
    assert info["provider"] == "google"
    assert info["id"] == "gemini-test-flash"


def test_get_active_model_info_flash_architecture(google_operator_llm_config):
    """Verify that flash profile returns Flash architecture."""
    info = ModelService.get_active_model_info("flash")
    assert info["name"] == "Flash"
    assert info["architecture"] == "ARTEMIS Flash"
    assert info["provider"] == "google"


def test_resolve_session_profile_from_device_info():
    """Verify profile resolution from device_info JSON."""
    row = {"device_info": json.dumps({"profile": "pro"})}
    assert ModelService.resolve_session_profile(row) == "pro"

    row_flash = {"device_info": json.dumps({"profile": "flash"})}
    assert ModelService.resolve_session_profile(row_flash) == "flash"


def test_resolve_session_profile_from_agent_names():
    """Verify profile resolution from agent/trace names."""
    row = {"device_info": None}
    assert ModelService.resolve_session_profile(row, agent_names=["planner", "operator"]) == "pro"
    assert ModelService.resolve_session_profile(row, agent_names=["flashrunner"]) == "flash"
