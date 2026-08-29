"""Offline tests for the teaching pipeline (migrated from scripts/test_teaching.py).

Judge layer is faked; citation alignment is patched to a fixed neutral verdict
so the deterministic formula expectation (0.4×10 + 0.6×5 = 7) stays stable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FAKE_JUDGE_RESPONSE = json.dumps(
    {
        "stage": "DS",
        "capability_scores": {
            "rule_retrieval": {
                "score": 8, "rationale": "引用正确",
                "evidence_quote": "依据《刑法》第二百六十四条",
            },
            "subsumption": {"score": 6, "rationale": "要件涵摄部分展开", "evidence_quote": ""},
            "claim_construction": {"score": 6, "rationale": "有从轻建议", "evidence_quote": ""},
            "evidence_marshalling": {"score": 5, "rationale": "证据组织不足", "evidence_quote": ""},
            "position_consistency": {"score": 7, "rationale": "立场一致", "evidence_quote": ""},
            "fact_identification": {"score": 7, "rationale": "识别了初犯情节", "evidence_quote": ""},
        },
        "subsumption_table": [
            {"element": "非法占有目的", "fact_found": "盗窃财物", "conclusion": "符合", "comment": ""}
        ],
        "knowledge_verdicts": [{"kp": "盗窃罪构成要件", "status": "partial", "reason": ""}],
        "error_tags": ["法条引用错误-264与266混淆"],
        "knowledge_gaps": ["盗窃罪构成要件"],
        "overall_feedback": "你的法条引用正确，建议补充构成要件逐项分析。",
    },
    ensure_ascii=False,
)


class FakeJudge:
    """Stand-in for the camel ChatAgent judge."""

    def __init__(self, _system_prompt: str) -> None:
        pass

    def step(self, _msg):  # noqa: ANN001 - mirrors camel response shape
        class _Msg:
            content = FAKE_JUDGE_RESPONSE

        class _Resp:
            msgs = [_Msg()]

        return _Resp()


def _make_case_dir(tmp_path: Path) -> Path:
    case_dir = tmp_path / "case_1"
    player_dir = case_dir / "_player_lawyer"
    player_dir.mkdir(parents=True)
    ledger = {
        "schema_version": "player-run-ledger-v1",
        "case_id": "case_1",
        "submissions": [
            {
                "request_id": "r1",
                "stage": "DS",
                "submission_type": "dialogue",
                "final_message": (
                    "被告人构成盗窃罪，依据《刑法》第二百六十四条，且系初犯，建议从轻处罚。"
                ),
                "submitted_at": "2026-08-21T10:00:00",
            }
        ],
    }
    (player_dir / "player_run_ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False), encoding="utf-8"
    )
    (case_dir / "DS_result.json").write_text(
        json.dumps(
            {
                "dialog_history": [
                    {"role": "client", "content": "被告人盗窃了财物。"},
                    {"role": "lawyer", "content": "被告人构成盗窃罪，依据《刑法》第二百六十四条。"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return case_dir


@pytest.fixture()
def patched_alignment(monkeypatch: pytest.MonkeyPatch):
    """Fix alignment verdicts to `neutral` for formula stability."""
    from src.teaching import scorer as scorer_module

    def _fake_verify(utterances, judge_client=None):  # noqa: ANN001, ARG001
        return {
            "items": [
                {
                    "sentence": u,
                    "citation": "《刑法》第二百六十四条",
                    "verdict": "neutral",
                }
                for u in utterances
            ],
            "summary": {"supports": 0, "contradicts": 0, "neutral": len(utterances)},
        }

    monkeypatch.setattr(scorer_module.citation_alignment, "verify_alignment", _fake_verify)


class TestRubrics:
    def test_eight_capabilities_six_stages(self) -> None:
        from src.teaching.rubrics import (
            CAPABILITIES,
            STAGE_CAPABILITY_MATRIX,
            validate_rubrics,
        )

        validate_rubrics()
        assert len(CAPABILITIES) == 8
        assert len(STAGE_CAPABILITY_MATRIX) == 6

    def test_stage_weights_sum_reasonably(self) -> None:
        from src.teaching.rubrics import stage_capability_weights

        weights = stage_capability_weights("DS")
        assert weights, "DS stage must have capability weights"
        assert all(w in (0.5, 1.0) for w in weights.values())


class TestLawCorpus:
    def test_corpus_available(self) -> None:
        from src.teaching import law_corpus

        stats = law_corpus.corpus_stats()
        assert stats["available"], "corpus missing"
        assert stats["total_articles"] >= 700

    def test_retrieval_finds_self_defense(self) -> None:
        from src.teaching import law_corpus

        hits = law_corpus.search_law("正当防卫 不负刑事责任", top_k=3)
        assert hits and hits[0]["article_ref"] == "第二十条"

    @pytest.mark.parametrize(
        ("title", "article", "expected"),
        [
            ("刑法", "第二百六十四条", "valid"),
            ("刑法", "第九千条", "invalid_article"),
            ("公司法", "第二百六十四条", "invalid_title"),
        ],
    )
    def test_citation_verification(self, title: str, article: str, expected: str) -> None:
        from src.teaching import law_corpus

        assert law_corpus.verify_citation(title, article)["status"] == expected


class TestCitationCheck:
    def test_wrong_article_flagged(self) -> None:
        from src.teaching.citation_check import check_submission_citations

        feedback = check_submission_citations(
            "依据《刑法》第二百六十四条构成盗窃罪，同时参照《刑法》第二千六十四条。"
        )
        assert feedback and feedback["status"] == "warn"
        assert any("第二千六十四条" in msg for msg in feedback["messages"])


class TestScorerPipeline:
    def test_transcript_extracts_student_utterances(self, tmp_path: Path) -> None:
        from src.teaching.transcript import build_scoring_input

        case_dir = _make_case_dir(tmp_path)
        scoring_input = build_scoring_input("case_1", "DS", case_dir)
        assert scoring_input["utterance_count"] == 1

    def test_full_scoring_flow(
        self, tmp_path: Path, patched_alignment: None
    ) -> None:
        from src.teaching.scorer import TeachingScorer

        case_dir = _make_case_dir(tmp_path)
        scorer = TeachingScorer(judge_factory=FakeJudge)
        event = scorer.score_stage(
            case_id="case_1", stage="DS", case_output_dir=case_dir, student_id="tester"
        )
        assert event, "scorer returned None"

        # deterministic layer: 0.4×base(10) + 0.6×semantic(neutral→5) = 7
        rr = event["capability_scores"]["rule_retrieval"]
        assert rr["score"] == 0.7
        assert rr["source"] == "deterministic"
        assert rr.get("judge_raw_score") == 8, "judge score kept for cross-audit"
        assert any(c["status"] == "valid" for c in event["law_citations"])
        assert any(
            c["article_ref"] == "第二百六十四条" for c in event["law_citations"]
        )
        assert (case_dir / "teaching" / "DS_learning_event.json").exists()

    def test_evidence_quote_verification(self) -> None:
        from src.teaching.scorer import TeachingScorer

        cs = {
            "subsumption": {
                "score": 0.6, "source": "judge",
                "evidence_quote": "且系初犯，建议从轻处罚",
            },
            "claim_construction": {
                "score": 0.6, "source": "judge",
                "evidence_quote": "学生从未说过这句话",
            },
        }
        TeachingScorer._verify_evidence_quotes(
            cs, ["被告人构成盗窃罪，依据《刑法》第二百六十四条，且系初犯，建议从轻处罚。"]
        )
        assert "unverified" not in cs["subsumption"]
        assert cs["claim_construction"].get("unverified") is True


class TestLearnerProfile:
    def _scored_event(self, tmp_path: Path, patched_alignment: None) -> dict:
        from src.teaching.scorer import TeachingScorer

        case_dir = _make_case_dir(tmp_path)
        event = TeachingScorer(judge_factory=FakeJudge).score_stage(
            case_id="case_1", stage="DS", case_output_dir=case_dir, student_id="tester"
        )
        assert event
        return event

    def test_profile_update_and_report(
        self, tmp_path: Path, patched_alignment: None
    ) -> None:
        from src.teaching import learner
        from src.teaching.report import build_report

        event = self._scored_event(tmp_path, patched_alignment)
        profile = learner.update_profile("tester", event)
        assert profile["capability_means"]["subsumption"] > 0

        report = build_report("tester")
        assert len(report["capability_radar"]) == 8

        # growth curve caliber: weighted mean, identical to radar
        scores_w = {
            code: (entry.get("score") or 0.0, entry.get("weight") or 0.5)
            for code, entry in event["capability_scores"].items()
            if entry.get("score") is not None
        }
        expected = sum(s * w for s, w in scores_w.values()) / sum(
            w for _s, w in scores_w.values()
        )
        assert any(
            abs(g["mean"] - round(expected, 3)) < 1e-9 for g in profile["growth_curve"]
        )

    def test_abstained_capability_excluded(
        self, tmp_path: Path, patched_alignment: None
    ) -> None:
        from src.teaching import learner

        event = self._scored_event(tmp_path, patched_alignment)
        event2 = dict(event)
        event2["capability_scores"] = dict(event["capability_scores"])
        event2["capability_scores"]["subsumption"] = {
            "score": None, "raw": None, "weight": 1.0, "source": "missing",
            "rationale": "", "evidence_quote": "",
        }
        profile2 = learner.update_profile("tester_abstain", event2)
        assert "subsumption" not in profile2["capability_means"], (
            "abstained capability must not count as 0"
        )
