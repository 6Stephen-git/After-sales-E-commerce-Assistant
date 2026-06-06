"""credential_trust 与 text_signals 单测。"""

from __future__ import annotations

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from schemas import CREDENTIAL_TRUST_SUSPECT, CREDENTIAL_TRUST_UNKNOWN, FactOutput

from backend.tools.text_signals import facts_has_deceptive_credential_clues, read_credential_trust


def test_credential_trust_suspect_triggers_deceptive() -> None:
    facts = FactOutput(credential_trust=CREDENTIAL_TRUST_SUSPECT, issue_summary="批量购买30件")
    assert facts_has_deceptive_credential_clues(facts)


def test_issue_summary_alone_does_not_trigger() -> None:
    facts = FactOutput(
        credential_trust=CREDENTIAL_TRUST_UNKNOWN,
        issue_summary="批量购买30件表演服",
        red_flags=[],
        visual_observations=["与发货款式一致"],
    )
    assert not facts_has_deceptive_credential_clues(facts)
    assert read_credential_trust(facts) == CREDENTIAL_TRUST_UNKNOWN
