"""验证 Flash 主备模型切换；使用真实重试逻辑，不连接设备或外部服务。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from langchain_core.messages import AIMessageChunk, HumanMessage
import pytest

from artemis.agents.flash import runner as runner_module
from artemis.agents.flash.runner import FlashRunner
from artemis.llm.reliability import LLMExhaustedError, LLMPermanentError
from artemis.llm.router import ModelEndpoint, ModelProvider
from artemis.services import llm as llm_service
from artemis.services.llm import RobustChatModelWrapper


class FakeModel:
    """记录真实包装层传来的消息和工具，按指定结果返回或抛错。"""

    def __init__(self, *, response="完成", error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.bound_tools = []

    def bind_tools(self, tools, **kwargs):
        self.bound_tools.append(tools)
        return self

    async def astream(self, messages, **kwargs):
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        yield AIMessageChunk(content=self.response)


class ServiceUnavailable(Exception):
    status_code = 503


class BadRequest(Exception):
    status_code = 400


@pytest.fixture
def recovery_state(monkeypatch, tmp_path):
    """隔离暂停文件和重试状态，让失败场景不影响其他测试或运行任务。"""
    events = []
    token = llm_service._FALLBACK_AVAILABLE.set(False)
    monkeypatch.setattr(llm_service, "PAUSE_FILE", tmp_path / ".artemis_paused")
    monkeypatch.setattr(llm_service, "_get_current_data_engine", lambda: None)
    monkeypatch.setattr(
        llm_service,
        "retry_policy_for",
        lambda category: SimpleNamespace(max_attempts=2, delay_for=lambda attempt: 0.0),
    )
    monkeypatch.setattr(
        llm_service,
        "_record_llm_event",
        lambda name, payload, **kwargs: events.append((name, payload)),
    )
    llm_service._ENDPOINT_BREAKER._states.clear()
    llm_service._NON_STREAMING_ENDPOINTS.clear()
    yield events
    llm_service._FALLBACK_AVAILABLE.reset(token)
    llm_service._ENDPOINT_BREAKER._states.clear()
    llm_service._NON_STREAMING_ENDPOINTS.clear()


def make_runner(main, backup):
    """只创建调用模型所需状态，跳过设备控制器初始化。"""
    runner = object.__new__(FlashRunner)
    main_wrapper = RobustChatModelWrapper(
        main, endpoint=ModelEndpoint(provider=ModelProvider.OPENAI, model_name="primary")
    )
    runner._llm_fallback = RobustChatModelWrapper(
        backup, endpoint=ModelEndpoint(provider=ModelProvider.OPENAI, model_name="backup")
    )
    return runner, main_wrapper


def test_init_loads_operator_fallback(monkeypatch):
    runner = object.__new__(FlashRunner)
    runner.ctx = object()
    primary, backup = object(), object()
    get_llm = Mock(side_effect=[primary, backup])
    monkeypatch.setattr(runner_module, "get_llm", get_llm)

    assert runner._init_llm() is primary
    assert runner._llm_fallback is backup
    assert get_llm.call_args_list[0].kwargs == {"name": "operator"}
    assert get_llm.call_args_list[1].kwargs == {"name": "operator", "use_fallback": True}


@pytest.mark.asyncio
async def test_primary_success_does_not_call_fallback(recovery_state):
    primary, backup = FakeModel(response="主模型结果"), FakeModel()
    runner, model = make_runner(primary, backup)
    result = await runner._invoke_model(model, [], [HumanMessage(content="任务")])

    assert result.content == "主模型结果"
    assert len(primary.calls) == 1
    assert not backup.calls
    assert not backup.bound_tools
    assert runner._llm is model
    assert not llm_service.PAUSE_FILE.exists()


@pytest.mark.asyncio
async def test_exhausted_primary_uses_fallback_with_same_tools_and_messages(recovery_state):
    primary = FakeModel(error=ServiceUnavailable("No available channel"))
    backup = FakeModel(response="备用模型结果")
    runner, model = make_runner(primary, backup)
    tools = [{"name": "report_task_status", "description": "报告结果", "parameters": {}}]
    messages = [HumanMessage(content="任务")]

    result = await runner._invoke_model(model, tools, messages)

    assert result.content == "备用模型结果"
    assert len(primary.calls) == 2
    assert backup.calls == [messages]
    assert primary.bound_tools == backup.bound_tools == [tools]
    assert all(call is messages for call in primary.calls + backup.calls)
    assert runner._llm is runner._llm_fallback
    assert not llm_service.PAUSE_FILE.exists()
    assert any(name == "llm_fallback" for name, _ in recovery_state)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [BadRequest("invalid payload"), asyncio.CancelledError()])
async def test_bad_request_and_cancellation_do_not_use_fallback(recovery_state, error):
    primary, backup = FakeModel(error=error), FakeModel()
    runner, model = make_runner(primary, backup)
    expected = (
        asyncio.CancelledError if isinstance(error, asyncio.CancelledError) else LLMPermanentError
    )
    with pytest.raises(expected):
        await runner._invoke_model(model, [], [])

    assert len(primary.calls) == 1
    assert not backup.calls
    assert not llm_service.PAUSE_FILE.exists()
    assert not llm_service._FALLBACK_AVAILABLE.get()
    assert all(name != "llm_fallback" for name, _ in recovery_state)


@pytest.mark.asyncio
async def test_both_models_fail_preserves_existing_pause_policy(recovery_state, monkeypatch):
    primary = FakeModel(error=ServiceUnavailable("primary unavailable"))
    backup = FakeModel(error=ServiceUnavailable("backup unavailable"))
    runner, model = make_runner(primary, backup)
    paused = []

    async def expire_pause(pause_file):
        paused.append(pause_file.read_text())
        return False

    monkeypatch.setattr(llm_service, "_wait_for_resume", expire_pause)
    with pytest.raises(LLMExhaustedError, match="backup unavailable"):
        await runner._invoke_model(model, [], [])

    assert len(primary.calls) == len(backup.calls) == 2
    assert len(paused) == 1
    assert "backup unavailable" in paused[0]
    assert not llm_service.PAUSE_FILE.exists()
