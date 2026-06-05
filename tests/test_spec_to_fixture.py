"""scenario_spec -> fixture 的字段契约测试。"""

import json

from spec_to_fixture import fixture_from_spec_dict


def _base_spec() -> dict:
    return {
        "meta": {"case_id": "T-SCENARIO", "title": "test"},
        "human_review": {
            "scenario_restated": "test",
            "fixture_focus": "test",
            "checks_before_run": ["test"],
        },
        "expectation": {},
        "taxonomy": {"evidence_level": "medium"},
        "materials": {"order_amount": 100},
        "buyer_profile": {},
        "evidence_facts": {
            "issue_summary": "买家主张七天无理由退货，争议点是商品完好。",
            "defect_type": "无瑕疵",
            "evidence_quality": "medium",
            "media_present": True,
            "visual_observations": ["吊牌完好无污渍"],
            "visual_defect_severity": "minor",
            "visual_goods_recoverability": "resalable",
            "logistics": {"goods_received": True, "logistics_normal": True},
        },
        "facts_override": {"compensation_ratio_cap": 0.15},
    }


def test_fact_override_defaults_to_replace_mode() -> None:
    """场景 spec 已给事实还原时，不应再混入 Agent1 文本不确定性。"""
    case = fixture_from_spec_dict(_base_spec())["cases"][0]

    assert case["test_overrides"]["agent1_mode"] == "replace"
    fo = case["facts_override"]
    assert fo["defect_type"] == "无瑕疵"
    assert fo["visual_defect_severity"] == "minor"
    assert fo["compensation_ratio_cap"] == 0.15


def test_evidence_facts_maps_logistics_and_media() -> None:
    """事实证据栏应写入 facts_override 与 materials 物流字段。"""
    spec = _base_spec()
    spec["evidence_facts"]["logistics"] = {
        "goods_received": True,
        "time_since_delivery_hours": 48,
        "note": "签收后48小时申请",
    }
    case = fixture_from_spec_dict(spec)["cases"][0]
    assert case["facts_override"]["goods_received"] is True
    assert case["facts_override"]["attributes"]["rule_context"]["time_since_delivery_hours"] == 48
    assert case["materials"]["is_received"] is True


def test_buyer_profile_refund_only_rate_preserved() -> None:
    """仅退款率与退货并退款率应分别进入 fixture。"""
    spec = _base_spec()
    spec["buyer_profile"] = {
        "purchase_count": 5,
        "return_rate": 0.4,
        "refund_only_rate": 0.4,
        "dispute_rate": 0.4,
        "dispute_count": 2,
    }
    profile = fixture_from_spec_dict(spec)["cases"][0]["buyer_profile"]
    assert profile["return_rate"] == 0.4
    assert profile["refund_only_rate"] == 0.4


def test_explicit_agent1_mode_is_preserved() -> None:
    """手动指定 merge_text 时保留，用于确实想混合 Agent1 文本抽取的测试。"""
    spec = _base_spec()
    spec["test_overrides"] = {"agent1_mode": "merge_text"}

    case = fixture_from_spec_dict(spec)["cases"][0]

    assert case["test_overrides"]["agent1_mode"] == "merge_text"


def test_expectation_blocks_do_not_enter_manual_fixture() -> None:
    """商家期望/禁忌只供评测，不得进入 Agent 跑批输入。"""
    spec = _base_spec()
    spec["scenario_narrative"] = "不要让业务链路读取这段情景叙事"
    spec["human_review"] = {
        "scenario_restated": "不要进入 fixture 的复述",
        "fixture_focus": "不要进入 fixture 的审阅重点",
        "checks_before_run": ["不要进入 fixture 的检查项"],
    }
    spec["expectation"] = {
        "intent_summary": "不要进入 fixture 的期望策略",
        "acceptable_dispositions": ["defend"],
        "forbidden_outputs": ["不要进入 fixture 的禁忌输出"],
    }

    payload = fixture_from_spec_dict(spec)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "不要让业务链路读取这段情景叙事" not in serialized
    assert "不要进入 fixture 的复述" not in serialized
    assert "不要进入 fixture 的审阅重点" not in serialized
    assert "不要进入 fixture 的检查项" not in serialized
    assert "不要进入 fixture 的期望策略" not in serialized
    assert "不要进入 fixture 的禁忌输出" not in serialized


def test_test_overrides_aliases_normalize_for_customer_value() -> None:
    """情景 LLM 自造别名应归一为跑批识别的客户价值阈值键。"""
    spec = _base_spec()
    spec["test_overrides"] = {
        "old_customer_value": "累计消费150元",
        "order_amount_threshold": "本单金额通道门槛100",
    }

    overrides = fixture_from_spec_dict(spec)["cases"][0]["test_overrides"]

    assert overrides["channel_threshold"] == 150
    assert overrides["order_value_amount_only_threshold"] == 100
    assert overrides["customer_lifetime_value"] == 150


def test_service_tag_short_name_normalizes_to_rule_lexicon_name() -> None:
    """测试场景短服务标应归一为规则索引标准名，确保 E 通道可被激活。"""
    spec = _base_spec()
    spec["materials"] = {
        "order_amount": 1000,
        "category": "宠物",
        "platform_service_tags": ["伤亡大病包退"],
    }

    payload = fixture_from_spec_dict(spec)
    tags = payload["cases"][0]["materials"]["platform_service_tags"]

    assert tags == ["“伤亡大病包退”服务规范"]


def test_invalid_service_tag_slug_inferred_from_spec_context() -> None:
    """生成器自造英文 slug 时，应从标题/标签上下文补全 lexicon 服务标。"""
    spec = _base_spec()
    spec["meta"]["title"] = "宠物伤亡大病包退争议"
    spec["meta"]["tags"] = ["伤亡大病包退", "宠物"]
    spec["materials"] = {
        "order_amount": 1000,
        "platform_service_tags": ["defect_guarantee"],
    }

    payload = fixture_from_spec_dict(spec)
    tags = payload["cases"][0]["materials"]["platform_service_tags"]

    assert tags == ["“伤亡大病包退”服务规范"]
