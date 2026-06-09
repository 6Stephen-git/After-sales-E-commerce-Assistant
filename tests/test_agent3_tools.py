"""Agent3 话术工具：风格与金额质量校验。"""

from backend.tools import agent3_tools as tools


def test_hedging_phrase_triggers_forbidden():
  """踢皮球式「能不能处理」应被拦截。"""
  assert tools._contains_forbidden_phrase("我帮您进一步核对，看能不能处理。")


def test_premature_rule_exposure_on_rule_explain():
  """非终局抗辩时引用条文时效应触发风格校验。"""
  script = "根据坏单包退规则，签收后48小时内申请才行。"
  payload = {"action_type": "rule_explain", "malicious_risk_level": "high"}
  assert tools._has_premature_rule_exposure(script, payload)


def test_explicit_rule_allowed_on_defend_prepare_high_risk():
  """终局抗辩阶段允许较直接说明规则边界。"""
  script = "根据平台规则，当前不满足直接退款条件。"
  payload = {"action_type": "defend_prepare", "malicious_risk_level": "high"}
  assert not tools._has_premature_rule_exposure(script, payload)
