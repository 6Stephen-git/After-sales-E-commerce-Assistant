"""
Agent 1 工具：图片分析。

约束：真实调用必须读环境变量中的端点与密钥；禁止在业务代码里硬编码 URL。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import httpx

LOG_PREFIX = "[Agent1工具]"
logger = logging.getLogger(__name__)


# ---------- 测试/联调：mock:// 前缀走本地固定返回，不落网 ----------
def _mock_analyze_image(image_url: str) -> dict[str, Any]:
    """
    离线模拟多模态返回结构，仅用于测试或本地联调。

    URL 须以 `mock://` 开头，其后标识决定返回哪套视觉字段；未识别标识时返回 error 字典。

    参数:
        image_url: 形如 mock://tear-tag 的测试地址。

    返回:
        与真实接口对齐的字段 dict（含 defect_type、edge_condition 等），
        或 `{"error": "中文原因"}`。
    """
    key = image_url.replace("mock://", "").strip().lower()
    mock_map: dict[str, dict[str, Any]] = {
        "tear-tag": {
            "defect_type": "破洞",
            "defect_location": "衣袖侧边",
            "edge_condition": "毛糙",
            "has_tag": True,
            "background": "桌面",
            "wear_signs": "无明显穿着痕迹",
        },
        "stain-no-tag": {
            "defect_type": "污渍",
            "defect_location": "胸前",
            "edge_condition": "无法判断",
            "has_tag": False,
            "background": "床上",
            "wear_signs": "有轻微折痕",
        },
        "clean-tag": {
            "defect_type": "无瑕疵",
            "defect_location": "无法判断",
            "edge_condition": "无法判断",
            "has_tag": True,
            "background": "桌面",
            "wear_signs": "无明显穿着痕迹",
        },
    }
    return mock_map.get(key, {"error": f"图片分析失败：未识别的 mock 图片标识 {key}"})


# ---------- 端点识别：阿里云百炼多模态 generation 走专用协议 ----------
def _is_dashscope_multimodal_endpoint(endpoint: str) -> bool:
    """
    判断是否为 DashScope multimodal-generation 地址，以决定请求体格式。

    参数:
        endpoint: 环境变量中的完整 URL。

    返回:
        路径包含 multimodal-generation 或为 dashscope 域名且包含 generation 时视为百炼多模态。
    """
    lower = endpoint.lower()
    if "multimodal-generation" in lower:
        return True
    return "dashscope" in lower and "generation" in lower


# ---------- 响应文本：从百炼 choices 中取出 assistant 的文本 ----------
def _dashscope_extract_assistant_text(response_data: dict[str, Any]) -> str | None:
    """
    解析 DashScope 响应，合并 assistant message 中的文本片段。

    VL 模型下 content 可能为含多个 dict 的列表；纯文本模型下可能为 string。

    参数:
        response_data: HTTP JSON 根对象。

    返回:
        拼接后的文本；无法解析时返回 None。
    """
    output = response_data.get("output")
    if not isinstance(output, dict):
        return None
    choices = output.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("text")
                if isinstance(t, str) and t:
                    parts.append(t)
        merged = "".join(parts).strip()
        return merged or None
    return None


# ---------- JSON 提取：从模型输出中解析 FactOutput 所需键 ----------
def _extract_json_object_from_text(text: str) -> dict[str, Any] | None:
    """
    从模型原始字符串中提取 JSON 对象（兼容 Markdown 代码块包裹）。

    参数:
        text: 模型输出全文。

    返回:
        解析出的 dict；失败返回 None。
    """
    stripped = text.strip()
    if not stripped:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if fence:
        stripped = fence.group(1).strip()
    try:
        obj = json.loads(stripped)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(stripped[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


# ---------- 类型规范化：布尔与字符串字段转为工具约定结构 ----------
def _coerce_has_tag(value: Any) -> bool | None:
    """
    将 JSON 中的 has_tag 转为布尔或无法解析时 None。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "yes", "是", "1"):
            return True
        if v in ("false", "no", "否", "0"):
            return False
    return None


# ---------- 字段映射：百炼 JSON → analyze_image 统一返回键 ----------
def _normalize_vision_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """
    将模型 JSON 规范为 Agent1 期望的键名与类型。

    参数:
        raw: 模型解析出的字典。

    返回:
        含 defect_type、edge_condition、has_tag 等键的 dict；无效项省略。
    """
    out: dict[str, Any] = {}
    for key in ("defect_type", "defect_location", "edge_condition", "background", "wear_signs"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    tag = _coerce_has_tag(raw.get("has_tag"))
    if tag is not None:
        out["has_tag"] = tag
    return out


# ---------- 百炼请求体：OpenAI 兼容 multimodal messages 结构 ----------
def _build_dashscope_payload(image_ref: str, model: str) -> dict[str, Any]:
    """
    构造 DashScope multimodal-generation 请求 JSON。

    参数:
        image_ref: 公网 URL 或 data:image/...;base64,... 。
        model: 百炼控制台模型名。

    返回:
        可作为 json= 发送的 dict。
    """
    vision_prompt = (
        "你是电商纠纷举证图片分析助手。请仅依据图片给出客观视觉特征，输出一个 JSON 对象，不要其它说明文字。\n"
        "JSON 字段与取值要求：\n"
        "- defect_type: 字符串，取值为「破洞」「污渍」「色差」「线头」「功能故障」「无瑕疵」之一，无法判断时填「无法判断」\n"
        "- defect_location: 字符串，瑕疵所在部位，无法判断填「无法判断」\n"
        "- edge_condition: 字符串，取值为「整齐」「毛糙」「无法判断」之一\n"
        "- has_tag: 布尔，图片中是否清晰可见吊牌\n"
        "- background: 字符串，拍摄背景简述\n"
        "- wear_signs: 字符串，穿着/使用痕迹描述，没有明显痕迹则填「无明显穿着痕迹」\n"
        "只输出 JSON。"
    )
    return {
        "model": model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"image": image_ref}, {"text": vision_prompt}],
                }
            ]
        },
    }


# ---------- 百炼调用：HTTP POST、解析 JSON 事实、统一错误返回 ----------
def _analyze_image_dashscope(
    *,
    endpoint: str,
    api_key: str,
    image_ref: str,
    model: str,
) -> dict[str, Any]:
    """
    调用阿里云百炼多模态接口，将模型返回文本解析为结构化事实字段。

    参数:
        endpoint: VISION_API_ENDPOINT。
        api_key: VISION_API_KEY。
        image_ref: 图片 URL 或 data URL。
        model: VISION_API_MODEL。

    返回:
        成功为视觉特征 dict；失败为 `{"error": "中文原因"}`。
    """
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = _build_dashscope_payload(image_ref=image_ref, model=model)
    backoff_seconds = [0.2, 0.4, 0.8]

    logger.info("%s 开始调用百炼多模态，model=%s", LOG_PREFIX, model)
    for attempt in range(3):
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                detail = exc.response.text[:800]
            except Exception:  # noqa: BLE001
                detail = str(exc)
            logger.error(
                "%s 第%d次调用失败：HTTP 状态异常，status=%s，详情=%s",
                LOG_PREFIX,
                attempt + 1,
                exc.response.status_code,
                detail,
            )
            if attempt == 2:
                return {"error": f"图片分析失败：多模态接口 HTTP {exc.response.status_code}，{detail}"}
            time.sleep(backoff_seconds[attempt])
            continue
        except httpx.RequestError as exc:
            logger.error("%s 第%d次调用失败：网络异常，原因=%s", LOG_PREFIX, attempt + 1, exc)
            if attempt == 2:
                return {"error": f"图片分析失败：调用多模态接口网络异常：{exc}"}
            time.sleep(backoff_seconds[attempt])
            continue
        except ValueError as exc:
            logger.error("%s 第%d次调用失败：响应非 JSON，原因=%s", LOG_PREFIX, attempt + 1, exc)
            if attempt == 2:
                return {"error": f"图片分析失败：接口返回非 JSON：{exc}"}
            time.sleep(backoff_seconds[attempt])
            continue

        if not isinstance(data, dict):
            if attempt == 2:
                return {"error": "图片分析失败：接口返回结构不符合约定"}
            time.sleep(backoff_seconds[attempt])
            continue

        if data.get("code"):
            msg = str(data.get("message", "")).strip()
            logger.error("%s 接口业务错误：code=%s message=%s", LOG_PREFIX, data.get("code"), msg)
            return {"error": f"图片分析失败：多模态接口返回错误 {data.get('code')} {msg}".strip()}

        assistant_text = _dashscope_extract_assistant_text(response_data=data)
        if not assistant_text:
            logger.error("%s 第%d次调用失败：响应中无 assistant 文本", LOG_PREFIX, attempt + 1)
            if attempt == 2:
                return {"error": "图片分析失败：模型响应中无可用文本"}
            time.sleep(backoff_seconds[attempt])
            continue

        parsed = _extract_json_object_from_text(text=assistant_text)
        if not parsed:
            logger.error("%s 第%d次调用失败：无法从模型输出解析 JSON", LOG_PREFIX, attempt + 1)
            if attempt == 2:
                return {"error": "图片分析失败：模型输出不是合法 JSON"}
            time.sleep(backoff_seconds[attempt])
            continue

        normalized = _normalize_vision_dict(raw=parsed)
        if not normalized.get("defect_type"):
            logger.error("%s 第%d次调用失败：JSON 缺少 defect_type", LOG_PREFIX, attempt + 1)
            if attempt == 2:
                return {"error": "图片分析失败：模型 JSON 缺少 defect_type 字段"}
            time.sleep(backoff_seconds[attempt])
            continue

        logger.info("%s 百炼多模态解析成功，defect_type=%s", LOG_PREFIX, normalized.get("defect_type"))
        return normalized

    return {"error": "图片分析失败：未知错误"}


# ---------- 兼容网关：历史自定义 POST {"image_url": ...} 协议 ----------
def _analyze_image_legacy(*, endpoint: str, api_key: str, image_url: str) -> dict[str, Any]:
    """
    兼容旧版 HTTP 网关：请求体为 `{"image_url": ...}`，响应须含 defect_type 或嵌套 data/result。

    参数:
        endpoint: 自定义服务地址。
        api_key: 可选 Bearer。
        image_url: 图片地址。

    返回:
        视觉特征 dict 或 `{"error": "中文原因"}`。
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {"image_url": image_url}
    backoff_seconds = [0.2, 0.4, 0.8]

    for attempt in range(3):
        try:
            with httpx.Client(timeout=8.0) as client:
                response = client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("%s 第%d次调用失败：%s", LOG_PREFIX, attempt + 1, exc)
            if attempt == 2:
                return {"error": f"图片分析失败：调用多模态接口异常：{exc}"}
            time.sleep(backoff_seconds[attempt])
            continue

        if isinstance(data, dict):
            if "defect_type" in data:
                return data
            nested = data.get("data") or data.get("result")
            if isinstance(nested, dict) and "defect_type" in nested:
                return nested

        if attempt == 2:
            return {"error": "图片分析失败：接口返回结构不符合约定"}
        time.sleep(backoff_seconds[attempt])

    return {"error": "图片分析失败：未知错误"}


# ---------- 生产路径：读环境变量、HTTP POST、失败返回 error 字典（不抛） ----------
def analyze_image(image_url: str) -> dict[str, Any]:
    """
    调用多模态服务，从单张图片 URL 提取视觉事实字段。

    mock:// 走 _mock_analyze_image；百炼 DashScope 走专用 input.messages 协议并要求模型输出 JSON；
    其它 endpoint 仍按旧版 `{"image_url": ...}` 转发。

    参数:
        image_url: 公网可访问地址或 data:image/...;base64,... ；空字符串直接返回错误 dict。

    返回:
        视觉特征 dict 或统一错误结构 dict（不抛异常，便于 Agent1 合并进 uncertainty）。
    """
    if not image_url:
        return {"error": "图片分析失败：image_url 为空"}

    if image_url.startswith("mock://"):
        return _mock_analyze_image(image_url=image_url)

    endpoint = os.getenv("VISION_API_ENDPOINT", "").strip()
    api_key = os.getenv("VISION_API_KEY", "").strip()
    if not endpoint:
        return {"error": "图片分析失败：未配置环境变量 VISION_API_ENDPOINT"}
    if not api_key:
        return {"error": "图片分析失败：未配置环境变量 VISION_API_KEY"}

    if _is_dashscope_multimodal_endpoint(endpoint=endpoint):
        # 默认视觉模型与 .env.example 一致；覆盖时请设环境变量 VISION_API_MODEL
        model = os.getenv("VISION_API_MODEL", "").strip() or "qwen3-vl-flash"
        return _analyze_image_dashscope(
            endpoint=endpoint,
            api_key=api_key,
            image_ref=image_url,
            model=model,
        )

    return _analyze_image_legacy(endpoint=endpoint, api_key=api_key, image_url=image_url)
