"""DeepSeek 调用封装：只返回 JSON 对象（spec §8 的重试规则在这里实现）。"""

import asyncio
import json
import logging
from typing import Protocol

import openai

log = logging.getLogger(__name__)

API_RETRY_DELAYS = (1, 2)  # API 失败后再试 2 次


class LLMError(Exception):
    pass


class LLMBadJSON(LLMError):
    def __init__(self, raw: str):
        super().__init__("模型返回的不是合法的 JSON 对象")
        self.raw = raw


class LLM(Protocol):
    async def complete_json(
        self, *, purpose: str, model: str, messages: list[dict], max_tokens: int = 2000
    ) -> dict: ...


class DeepSeekLLM:
    def __init__(self, api_key: str, base_url: str, *, client=None, sleep=asyncio.sleep):
        # SDK 自带的重试关掉，统一由这里控制
        self._client = client or openai.AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)
        self._sleep = sleep

    async def complete_json(
        self, *, purpose: str, model: str, messages: list[dict], max_tokens: int = 2000
    ) -> dict:
        raw = ""
        for _ in range(2):  # 格式不对时重试 1 次
            raw = await self._create(purpose, model, messages, max_tokens)
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict):
                return data
        raise LLMBadJSON(raw)

    async def _create(self, purpose: str, model: str, messages: list[dict], max_tokens: int) -> str:
        for attempt in range(len(API_RETRY_DELAYS) + 1):
            try:
                resp = await self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
            except openai.APIError as e:
                if attempt == len(API_RETRY_DELAYS):
                    raise LLMError(f"调用 DeepSeek 失败：{e}") from e
                log.warning("DeepSeek 调用失败（%s），%s 秒后重试：%s", purpose, API_RETRY_DELAYS[attempt], e)
                await self._sleep(API_RETRY_DELAYS[attempt])
                continue
            usage = getattr(resp, "usage", None)
            log.info("llm purpose=%s model=%s usage=%s", purpose, model, usage)
            return resp.choices[0].message.content or ""
        raise AssertionError("unreachable")
