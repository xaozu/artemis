"""agents 单元测试共用的 fixture。"""

import pytest
from pydantic import SecretStr

from artemis.config.settings import settings


@pytest.fixture
def fake_google_api_key(monkeypatch):
    """注入占位 GOOGLE_API_KEY，让 Explorer / VideoAnalyzer 选择原生 Gemini 引擎。

    两者都按 ``settings.GOOGLE_API_KEY`` 是否存在决定走原生 google-genai 还是
    通用 LangChain 引擎。依赖 mock ``genai.Client`` 的用例必须显式声明这一前提，
    否则在没有配置 Google Key 的环境（例如只配了 OpenAI 兼容代理）里会落到
    通用引擎，再因 MagicMock 的 provider 报 ``Unknown LLM provider``。
    """
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", SecretStr("test-google-api-key"))
