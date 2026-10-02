from types import SimpleNamespace

import httpx
import openai
import pytest

from mira.fakes import EchoLLM, FakeLLM
from mira.llm import DeepSeekLLM, LLMBadJSON, LLMError

MSGS = [{"role": "user", "content": "hi json"}]


def conn_error():
    return openai.APIConnectionError(request=httpx.Request("POST", "https://x"))


class StubClient:
    def __init__(self, script):
        self.script = list(script)
        self.kwargs = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.kwargs.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=item))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )


def make(script):
    sleeps = []

    async def sleep(s):
        sleeps.append(s)

    client = StubClient(script)
    return DeepSeekLLM("k", "https://x", client=client, sleep=sleep), client, sleeps


async def call(llm):
    return await llm.complete_json(purpose="chat", model="m", messages=MSGS)


async def test_returns_parsed_json():
    llm, _, _ = make(['{"a": 1}'])
    assert await call(llm) == {"a": 1}


async def test_sends_json_response_format():
    llm, client, _ = make(['{"a": 1}'])
    await llm.complete_json(purpose="chat", model="m", messages=MSGS, max_tokens=77)
    kw = client.kwargs[0]
    assert kw["response_format"] == {"type": "json_object"}
    assert (kw["model"], kw["messages"], kw["max_tokens"]) == ("m", MSGS, 77)


async def test_retries_api_error_then_succeeds():
    llm, _, sleeps = make([conn_error(), conn_error(), '{"ok": true}'])
    assert await call(llm) == {"ok": True}
    assert sleeps == [1, 2]


async def test_raises_after_three_api_failures():
    llm, client, _ = make([conn_error(), conn_error(), conn_error()])
    with pytest.raises(LLMError) as e:
        await call(llm)
    assert not isinstance(e.value, LLMBadJSON) and len(client.kwargs) == 3


async def test_bad_json_retried_once_then_raises_with_raw():
    llm, client, _ = make(["不是json", "不是json"])
    with pytest.raises(LLMBadJSON) as e:
        await call(llm)
    assert e.value.raw == "不是json" and len(client.kwargs) == 2


async def test_non_object_json_is_bad():
    llm, _, _ = make(["[1, 2]", ""])
    with pytest.raises(LLMBadJSON):
        await call(llm)


async def test_fake_llm_script_and_calls():
    fake = FakeLLM([{"x": 1}, LLMError("down")])
    assert await fake.complete_json(purpose="writer", model="m", messages=MSGS) == {"x": 1}
    with pytest.raises(LLMError):
        await fake.complete_json(purpose="writer", model="m", messages=MSGS)
    assert [c["purpose"] for c in fake.calls] == ["writer", "writer"]


async def test_echo_llm_by_purpose():
    echo = EchoLLM()
    msgs = [{"role": "user", "content": "【背景】\n...\n\n【新消息】\n今天好累啊"}]
    chat = await echo.complete_json(purpose="chat", model="m", messages=msgs)
    assert chat["approach"] == "normal" and chat["messages"][0] == "收到：今天好累啊"
    assert (await echo.complete_json(purpose="writer", model="m", messages=msgs))["ops"] == []
    assert (await echo.complete_json(purpose="reflector", model="m", messages=msgs))["patterns"] == []
