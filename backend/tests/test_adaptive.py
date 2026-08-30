"""Tests for the adaptive review module (EduBrain planner integration)."""

from __future__ import annotations

import pytest


@pytest.fixture()
def adaptive_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIMLAW_ADAPTIVE_DATA_DIR", str(tmp_path / "adaptive"))
    import src.adaptive.service as service

    service._BANK_CACHE = None  # force reload against current data dir
    yield
    service._BANK_CACHE = None


class TestBank:
    def test_bank_loaded(self, adaptive_env: None) -> None:
        from src.adaptive import bank_status

        status = bank_status()
        assert status["available"]
        assert status["items"] >= 30
        assert status["knowledge_nodes"] == 10
        assert "正当防卫" in " ".join(status["knowledge_names"])


class TestPlanning:
    def test_diagnostic_cold_start(self, adaptive_env: None) -> None:
        from src.adaptive import plan_for_student

        plan = plan_for_student("quiz_user", mode="diagnostic", limit=5)
        recs = plan["recommendations"]
        assert 1 <= len(recs) <= 5
        # cold start: every recommendation must be a diagnostic pick
        assert all(r["reason_code"] == "no_evidence_collect_diagnostic" for r in recs)
        # no answer leakage in the plan payload
        assert all("answer" not in r for r in recs)

    def test_answer_then_replan_changes_reason(self, adaptive_env: None) -> None:
        from src.adaptive import answer_item, plan_for_student

        plan = plan_for_student("quiz_user", mode="diagnostic", limit=1)
        item_id = plan["recommendations"][0]["item_id"]
        knowledge_id = plan["recommendations"][0]["knowledge_id"]
        result = answer_item("quiz_user", item_id, "A")
        assert result["ok"]
        assert isinstance(result["correct"], bool)
        assert result["rationale"]  # teaching feedback attached

        plan2 = plan_for_student("quiz_user", mode="review", limit=5)
        recs2 = plan2["recommendations"]
        # answered item must never be recommended again
        assert all(r["item_id"] != item_id for r in recs2)
        # the answered knowledge node now carries evidence
        evidence = {row["knowledge_id"]: row for row in plan2["knowledge_evidence"]}
        assert evidence[knowledge_id]["event_count"] >= 1

    def test_unknown_item_rejected(self, adaptive_env: None) -> None:
        from src.adaptive import answer_item

        result = answer_item("quiz_user", "GEN_DOES_NOT_EXIST", "A")
        assert result["ok"] is False

    def test_history_accumulates(self, adaptive_env: None) -> None:
        from src.adaptive import answer_item, history_summary, plan_for_student

        plan = plan_for_student("counter_user", mode="diagnostic", limit=2)
        for rec in plan["recommendations"]:
            answer_item("counter_user", rec["item_id"], "B")
        summary = history_summary("counter_user")
        assert summary["total_answers"] == 2


class TestCaseWeaknessBoost:
    def test_review_mode_boosts_case_gaps(
        self, adaptive_env: None, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """精学画像的 missing 知识点应提升对应题目排名并打标。"""
        import json

        from src.adaptive import plan_for_student
        from src.adaptive.service import _load_bank
        import src.adaptive.service as service
        from src.teaching import learner

        # fabricate a learner profile: 正当防卫 mastered-level missing
        profiles = tmp_path / "profiles"
        profiles.mkdir()
        bank = _load_bank()
        node = next(n for n in bank["nodes"] if "正当防卫" in str(n.get("canonical_name")))
        profile = {
            "schema_version": "learner-profile-v1",
            "student_id": "boost_user",
            "knowledge_state": {
                str(node["canonical_name"]): {"latest": "missing", "exposed": 5},
            },
        }
        (profiles / "boost_user.json").write_text(
            json.dumps(profile, ensure_ascii=False), encoding="utf-8"
        )
        monkeypatch.setenv("SIMLAW_TEACHING_PROFILES_DIR", str(profiles))

        plan = plan_for_student("boost_user", mode="review", limit=10)
        recs = plan["recommendations"]
        boosted = [r for r in recs if r.get("case_weakness") == "missing"]
        assert boosted, "正当防卫 items must be boosted from the case profile"
        assert str(node["canonical_name"]) in plan.get("case_weakness_signals", {})

    def test_diagnostic_mode_ignores_case_profile(
        self, adaptive_env: None, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json

        from src.adaptive import plan_for_student

        profiles = tmp_path / "profiles"
        profiles.mkdir()
        profile = {
            "schema_version": "learner-profile-v1",
            "student_id": "diag_user",
            "knowledge_state": {"正当防卫": {"latest": "missing", "exposed": 5}},
        }
        (profiles / "diag_user.json").write_text(
            json.dumps(profile, ensure_ascii=False), encoding="utf-8"
        )
        monkeypatch.setenv("SIMLAW_TEACHING_PROFILES_DIR", str(profiles))

        plan = plan_for_student("diag_user", mode="diagnostic", limit=10)
        assert "case_weakness_signals" not in plan
        assert all(not r.get("case_weakness") for r in plan["recommendations"])
