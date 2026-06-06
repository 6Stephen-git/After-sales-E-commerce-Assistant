"""
情景生成/评判共用的 LLM 调用与 JSON 解析工具。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from backend.tools.llm_client import chat_completion

from eval.pipeline.paths import PROMPTS_DIR
LLM_UTILS_LOG_PREFIX = "[ScenarioLLM]"
logger = logging.getLogger(__name__)


def load_prompt(name: str) -> str:
    """读取 eval/content/prompts 下 Markdown 提示词。"""
    path = PROMPTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"{LLM_UTILS_LOG_PREFIX} 提示词不存在：{path}")
    return path.read_text(encoding="utf-8")


def load_json_prompt(name: str) -> dict[str, Any]:
    """读取 eval/content/prompts 下 JSON 文件。"""
    path = PROMPTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"{LLM_UTILS_LOG_PREFIX} JSON 提示词不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def extract_json_object(raw_text: str) -> dict[str, Any]:
    """
    从 LLM 文本中提取单个 JSON 对象（支持 fenced code block）。
    """
    normalized = (raw_text or "").strip()
    if not normalized:
        raise ValueError(f"{LLM_UTILS_LOG_PREFIX} LLM 返回为空，无法解析 JSON")

    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", normalized)
    if fence_match:
        normalized = fence_match.group(1).strip()
    elif normalized.startswith("```"):
        normalized = normalized.replace("```json", "").replace("```", "").strip()

    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{LLM_UTILS_LOG_PREFIX} JSON 解析失败：{exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"{LLM_UTILS_LOG_PREFIX} 根节点必须是 JSON 对象")
    return payload


def call_llm_json(
    *,
    system_prompt: str,
    user_prompt: str,
    model_env_key: str,
    fallback_model_env_key: str | None = None,
    temperature: float = 0.45,
) -> dict[str, Any]:
    """
    调用 LLM 并解析为 JSON 对象；未配置 API 或调用失败时抛 RuntimeError。
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    logger.info(
        "%s 开始请求 model_env=%s temperature=%s",
        LLM_UTILS_LOG_PREFIX,
        model_env_key,
        temperature,
    )
    raw = chat_completion(
        messages=messages,
        model_env_key=model_env_key,
        fallback_model_env_key=fallback_model_env_key,
        temperature=temperature,
    )
    if raw is None:
        raise RuntimeError(
            f"{LLM_UTILS_LOG_PREFIX} LLM 调用失败或未配置（需 LLM_API_ENDPOINT、LLM_API_KEY、{model_env_key}）"
        )
    logger.info("%s 请求成功，开始解析 JSON", LLM_UTILS_LOG_PREFIX)
    return extract_json_object(raw)


def scenario_gen_model_env() -> str:
    """情景生成模型环境变量名。"""
    explicit = os.getenv("SCENARIO_GEN_LLM_MODEL", "").strip()
    if explicit:
        return "SCENARIO_GEN_LLM_MODEL"
    return "AGENT2_LLM_MODEL"


def scenario_gen_temperature() -> float:
    """情景生成温度。"""
    raw = os.getenv("SCENARIO_GEN_TEMPERATURE", "0.45").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.45

