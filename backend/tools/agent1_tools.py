"""
Agent 1 工具：图片分析。

约束：真实调用必须读环境变量中的端点与密钥；禁止在业务代码里硬编码 URL。
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx


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


# ---------- 生产路径：读环境变量、HTTP POST、失败返回 error 字典（不抛） ----------
def analyze_image(image_url: str) -> dict[str, Any]:
    """
    调用多模态服务，从单张图片 URL 提取视觉事实字段。

    mock:// 走 _mock_analyze_image；否则读 VISION_API_ENDPOINT / VISION_API_KEY，
    POST JSON 请求体 `{"image_url": ...}`，最多重试 3 次、指数退避。
    成功时返回含 defect_type 等键的 dict；任一步失败返回 `{"error": "中文原因"}`。

    参数:
        image_url: 图片可访问地址；空字符串直接返回错误 dict。

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
            if attempt == 2:
                return {"error": f"图片分析失败：调用多模态接口异常：{exc}"}
            time.sleep(backoff_seconds[attempt])
            continue

        if isinstance(data, dict):
            # 兼容常见返回结构：顶层即字段，或 data/result 内嵌字段。
            if "defect_type" in data:
                return data
            nested = data.get("data") or data.get("result")
            if isinstance(nested, dict) and "defect_type" in nested:
                return nested

        if attempt == 2:
            return {"error": "图片分析失败：接口返回结构不符合约定"}
        time.sleep(backoff_seconds[attempt])

    return {"error": "图片分析失败：未知错误"}
