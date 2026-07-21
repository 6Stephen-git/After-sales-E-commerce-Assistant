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

from backend.cache.vision_cache import get_cached_vision, save_vision
from schemas import VALID_CREDENTIAL_TRUST

LOG_PREFIX = "[Agent1工具]"
logger = logging.getLogger(__name__)

# ---------- 视觉 prompt：诉求锚定 + 通用 schema（品类 slug 仅可见时输出） ----------
_VISION_TASK_BLOCK = (
    "任务：下方是买家的售后诉求。请只从本张图片中提取与该诉求直接相关的可见信息。\n"
    "原则：\n"
    "1) 以诉求为唯一锚点，不做与诉求无关的泛化描述；\n"
    "2) 图中能印证诉求的写清楚；看不清的明确写无法从本图确认；与诉求陈述明显不符的写入 visual_red_flags；\n"
    "3) 品类：仅当商品形态在图中清晰可见（如手机、鞋、食品包装）时，可输出 category_slug；"
    "看不清或仅能确认瑕疵但看不出商品类别时填 null；不得臆测。\n"
    "4) 只陈述客观可见事实，不做责任判定；描述用语优先对齐买家诉求中的说法。"
)

_VISION_JSON_SCHEMA_BASE = (
    "只输出 JSON，不要其它文字：\n"
    "- visual_description: 与买家诉求最相关的一两句视觉结论\n"
    "- findings: 字符串数组，2~4 条短句，说明图中哪些内容与诉求对应（可见/不可见/部分可见）\n"
    "- visual_red_flags: 与诉求陈述明显矛盾、或图源可疑（如水印/网图/非实拍环境）；无则 []\n"
    "- credential_trust: suspect|trusted|unknown — 仅判断本张举证图是否像网图/非本单实拍/明显伪造；"
    "与商品瑕疵无关；看不清或无法判断填 unknown\n"
    "- credential_trust_note: 字符串或 null，一句说明 credential_trust 的理由\n"
    "- defect_type: 字符串或 null，诉求所涉问题的客观现象描述\n"
    "- defect_location: 字符串或 null，该现象在图中的位置或区域\n"
    "- visual_defect_severity: minor|moderate|severe|null，据可见损毁判断问题严重程度；看不清填 null\n"
    "- visual_goods_recoverability: resalable|repairable|unrecoverable|null，"
    "据可见状态判断退回后能否再售或修复；看不清填 null\n"
    "- attributes: 对象，其它与诉求相关的可见细节；无则 {}"
)


def _build_vision_json_schema_block() -> str:
    """拼装视觉 JSON schema，含可选 category_slug 枚举。"""
    from backend.tools.rule_lexicon import format_category_slug_compact

    slug_compact = format_category_slug_compact()
    slug_line = ""
    if slug_compact:
        slug_line = (
            f"\n- category_slug: 字符串或 null，商品形态清晰可见时从 [{slug_compact}] 中选择；不确定填 null"
        )
    return f"{_VISION_JSON_SCHEMA_BASE}{slug_line}"

VISUAL_DEFECT_SEVERITY_VALUES = frozenset({"minor", "moderate", "severe"})
VISUAL_GOODS_RECOVERABILITY_VALUES = frozenset({"resalable", "repairable", "unrecoverable"})
CREDENTIAL_TRUST_VALUES = frozenset(VALID_CREDENTIAL_TRUST)


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


def _coerce_visual_enum(value: Any, valid_values: frozenset[str]) -> str | None:
    """将视觉枚举字段规范为小写合法值，非法则返回 None。"""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in valid_values else None


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
    visual_description = raw.get("visual_description")
    if isinstance(visual_description, str) and visual_description.strip():
        out["visual_description"] = visual_description.strip()

    findings = raw.get("findings")
    if isinstance(findings, list):
        normalized_findings: list[str] = []
        for item in findings:
            text = str(item or "").strip()
            if text:
                normalized_findings.append(text)
        if normalized_findings:
            out["findings"] = normalized_findings

    vflags = raw.get("visual_red_flags")
    if isinstance(vflags, list):
        vf_out: list[str] = []
        for item in vflags:
            t = str(item or "").strip()
            if t:
                vf_out.append(t)
        if vf_out:
            out["visual_red_flags"] = vf_out

    attributes = raw.get("attributes")
    if isinstance(attributes, dict):
        cleaned_attributes: dict[str, Any] = {}
        for key, value in attributes.items():
            key_text = str(key or "").strip()
            if key_text and value is not None:
                cleaned_attributes[key_text] = value
        if cleaned_attributes:
            out["attributes"] = cleaned_attributes

    for key in ("defect_type", "defect_location", "edge_condition", "background", "wear_signs"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()

    severity = _coerce_visual_enum(raw.get("visual_defect_severity"), VISUAL_DEFECT_SEVERITY_VALUES)
    if severity:
        out["visual_defect_severity"] = severity
    recoverability = _coerce_visual_enum(raw.get("visual_goods_recoverability"), VISUAL_GOODS_RECOVERABILITY_VALUES)
    if recoverability:
        out["visual_goods_recoverability"] = recoverability

    from backend.tools.rule_lexicon import validate_category_slug

    visual_slug = validate_category_slug(raw.get("category_slug"))
    if visual_slug:
        out["category_slug"] = visual_slug
    tag = _coerce_has_tag(raw.get("has_tag"))
    if tag is not None:
        out["has_tag"] = tag

    trust = _coerce_visual_enum(raw.get("credential_trust"), CREDENTIAL_TRUST_VALUES)
    if trust:
        out["credential_trust"] = trust
    trust_note = raw.get("credential_trust_note")
    if isinstance(trust_note, str) and trust_note.strip():
        out["credential_trust_note"] = trust_note.strip()

    return out


# ---------- 结果校验：半结构化模式下至少包含一种可用观察 ----------
def _has_meaningful_vision_content(normalized: dict[str, Any]) -> bool:
    """
    判断多模态结果是否包含可消费的视觉信息。

    仅认新模式字段：
    - visual_description
    - findings
    - attributes
    """
    if not isinstance(normalized, dict):
        return False
    if isinstance(normalized.get("visual_description"), str) and normalized["visual_description"].strip():
        return True
    findings = normalized.get("findings")
    if isinstance(findings, list) and any(str(item or "").strip() for item in findings):
        return True
    vflags = normalized.get("visual_red_flags")
    if isinstance(vflags, list) and any(str(item or "").strip() for item in vflags):
        return True
    attributes = normalized.get("attributes")
    if isinstance(attributes, dict) and len(attributes) > 0:
        return True
    return False


# ---------- 视觉分析指引：诉求锚定 prompt ----------
def _build_vision_analysis_prompt(guidance: str) -> str:
    """
    将买家诉求与任务说明合并为完整视觉 prompt。
    """
    claim_text = guidance.strip() or "（买家未提供文字诉求：请描述图中可能与售后争议相关的客观可见事实。）"
    return (
        "你是电商售后视觉分析助手。\n"
        f"{_VISION_TASK_BLOCK}\n"
        f"【买家诉求】\n{claim_text}\n"
        f"{_build_vision_json_schema_block()}"
    )


# ---------- 百炼请求体：OpenAI 兼容 multimodal messages 结构 ----------
def _build_dashscope_payload(image_ref: str, model: str, guidance: str = "") -> dict[str, Any]:
    """
    构造 DashScope multimodal-generation 请求 JSON。

    参数:
        image_ref: 公网 URL 或 data:image/...;base64,... 。
        model: 百炼控制台模型名。
        guidance: 事实 LLM 提炼后的买家诉求文本。

    返回:
        可作为 json= 发送的 dict。
    """
    vision_prompt = _build_vision_analysis_prompt(guidance=guidance)
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
    guidance: str = "",
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
    payload = _build_dashscope_payload(image_ref=image_ref, model=model, guidance=guidance)
    backoff_seconds = [0.2, 0.4, 0.8]

    logger.info("%s 开始调用百炼多模态，endpoint=%s model=%s", LOG_PREFIX, endpoint, model)
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
                "%s 第%d次调用失败：HTTP 状态异常，endpoint=%s model=%s status=%s，详情=%s",
                LOG_PREFIX,
                attempt + 1,
                endpoint,
                model,
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
        if not _has_meaningful_vision_content(normalized=normalized):
            logger.error("%s 第%d次调用失败：JSON 缺少可用视觉观察字段", LOG_PREFIX, attempt + 1)
            if attempt == 2:
                return {"error": "图片分析失败：模型 JSON 缺少可用视觉观察字段"}
            time.sleep(backoff_seconds[attempt])
            continue

        logger.info("%s 百炼多模态解析成功，summary=%s", LOG_PREFIX, normalized.get("visual_description", ""))
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
def analyze_image(image_url: str, guidance: str = "", merchant_id: str = "") -> dict[str, Any]:
    """
    调用多模态服务，从单张图片 URL 提取视觉事实字段。

    百炼 DashScope 走专用 input.messages 协议并要求模型输出 JSON；
    其它 endpoint 仍按旧版 `{"image_url": ...}` 转发。

    参数:
        image_url: 公网可访问地址或 data:image/...;base64,... ；空字符串直接返回错误 dict。
        merchant_id: 商家标识；为空时跳过视觉 Redis 缓存，避免跨租户复用。

    返回:
        视觉特征 dict 或统一错误结构 dict（不抛异常，便于 Agent1 合并进 uncertainty）。
    """
    if not image_url:
        return {"error": "图片分析失败：image_url 为空"}

    endpoint = os.getenv("VISION_API_ENDPOINT", "").strip()
    api_key = os.getenv("VISION_API_KEY", "").strip()
    if not endpoint:
        return {"error": "图片分析失败：未配置环境变量 VISION_API_ENDPOINT"}
    if not api_key:
        return {"error": "图片分析失败：未配置环境变量 VISION_API_KEY"}

    if _is_dashscope_multimodal_endpoint(endpoint=endpoint):
        # 默认视觉模型与 .env.example 一致；覆盖时请设环境变量 VISION_API_MODEL
        model = os.getenv("VISION_API_MODEL", "").strip() or "qwen3-vl-flash"
        logger.info("%s 视觉走百炼多模态协议，endpoint=%s model=%s", LOG_PREFIX, endpoint, model)
        cached = get_cached_vision(merchant_id, image_url, model, guidance)
        if cached is not None:
            return cached
        result = _analyze_image_dashscope(
            endpoint=endpoint,
            api_key=api_key,
            image_ref=image_url,
            model=model,
            guidance=guidance,
        )
        if not result.get("error"):
            save_vision(merchant_id, image_url, model, guidance, result)
        return result

    logger.warning(
        "%s 视觉走 legacy 协议（非百炼 endpoint），请确认 VISION_API_ENDPOINT 是否配置正确：%s",
        LOG_PREFIX,
        endpoint,
    )
    legacy_model = "legacy"
    cached = get_cached_vision(merchant_id, image_url, legacy_model, guidance)
    if cached is not None:
        return cached
    legacy_result = _analyze_image_legacy(endpoint=endpoint, api_key=api_key, image_url=image_url)
    if legacy_result.get("error"):
        return legacy_result
    result = _normalize_vision_dict(legacy_result)
    save_vision(merchant_id, image_url, legacy_model, guidance, result)
    return result
