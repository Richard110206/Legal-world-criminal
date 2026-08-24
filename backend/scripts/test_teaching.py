"""Offline functional tests for the teaching module (no LLM required).

Covers: rubrics validation, local law corpus + retrieval, citation check,
transcript assembly, scorer with a fake judge, learner profile, report.

Run:  cd backend && .venv\\Scripts\\python.exe -X utf8 scripts\\test_teaching.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    raise AssertionError(msg)


def test_rubrics() -> None:
    from src.teaching.rubrics import CAPABILITIES, STAGE_CAPABILITY_MATRIX, validate_rubrics

    validate_rubrics()
    assert len(CAPABILITIES) == 8
    assert len(STAGE_CAPABILITY_MATRIX) == 6
    _ok("rubrics: 8 能力 + 6 阶段矩阵")


def test_corpus() -> None:
    from src.teaching import law_corpus

    stats = law_corpus.corpus_stats()
    assert stats["available"], "corpus missing"
    assert stats["total_articles"] >= 700
    _ok(f"corpus: {stats['total_articles']} articles")

    hits = law_corpus.search_law("正当防卫 不负刑事责任", top_k=3)
    assert hits and hits[0]["article_ref"] == "第二十条"
    _ok("retrieval: 正当防卫 → 第二十条")

    assert law_corpus.verify_citation("刑法", "第二百六十四条")["status"] == "valid"
    assert law_corpus.verify_citation("刑法", "第九千条")["status"] == "invalid_article"
    assert law_corpus.verify_citation("公司法", "第二百六十四条")["status"] == "invalid_title"
    _ok("citation verify: valid / invalid_article / invalid_title")


def test_citation_check() -> None:
    from src.teaching.citation_check import check_submission_citations

    feedback = check_submission_citations("依据《刑法》第二百六十四条构成盗窃罪，同时参照《刑法》第二千六十四条。")
    assert feedback and feedback["status"] == "warn"
    assert any("第二千六十四条" in msg for msg in feedback["messages"])
    _ok("instant citation check catches wrong article")


def test_transcript_and_scorer() -> None:
    from src.teaching.scorer import TeachingScorer
    from src.teaching.transcript import build_scoring_input

    # fabricate a case output dir with a ledger + result file
    with tempfile.TemporaryDirectory() as tmp:
        case_dir = Path(tmp) / "case_1"
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
                    "final_message": "被告人构成盗窃罪，依据《刑法》第二百六十四条，且系初犯，建议从轻处罚。",
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

        scoring_input = build_scoring_input("case_1", "DS", case_dir)
        assert scoring_input["utterance_count"] == 1
        _ok("transcript: extracted 1 student utterance")

        fake_response = json.dumps(
            {
                "stage": "DS",
                "capability_scores": {
                    "rule_retrieval": {"score": 8, "rationale": "引用正确", "evidence_quote": "依据《刑法》第二百六十四条"},
                    "subsumption": {"score": 6, "rationale": "要件涵摄部分展开", "evidence_quote": ""},
                    "claim_construction": {"score": 6, "rationale": "有从轻建议", "evidence_quote": ""},
                    "evidence_marshalling": {"score": 5, "rationale": "证据组织不足", "evidence_quote": ""},
                    "position_consistency": {"score": 7, "rationale": "立场一致", "evidence_quote": ""},
                    "fact_identification": {"score": 7, "rationale": "识别了初犯情节", "evidence_quote": ""},
                },
                "subsumption_table": [{"element": "非法占有目的", "fact_found": "盗窃财物", "conclusion": "符合", "comment": ""}],
                "knowledge_verdicts": [{"kp": "盗窃罪构成要件", "status": "partial", "reason": ""}],
                "error_tags": ["法条引用错误-264与266混淆"],
                "knowledge_gaps": ["盗窃罪构成要件"],
                "overall_feedback": "你的法条引用正确，建议补充构成要件逐项分析。",
            },
            ensure_ascii=False,
        )

        class _FakeJudge:
            def __init__(self, _system_prompt):
                pass

            def step(self, _msg):
                class _M:
                    content = fake_response

                class _R:
                    msgs = [_M()]

                return _R()

        scorer = TeachingScorer(judge_factory=_FakeJudge)
        event = scorer.score_stage(case_id="case_1", stage="DS", case_output_dir=case_dir, student_id="tester")
        assert event, "scorer returned None"
        assert event["capability_scores"]["rule_retrieval"]["score"] == 0.8
        assert any(c["status"] == "valid" for c in event["law_citations"])
        assert any(c["article_ref"] == "第二百六十四条" for c in event["law_citations"])
        event_path = case_dir / "teaching" / "DS_learning_event.json"
        assert event_path.exists()
        _ok("scorer: LearningEvent written with normalized scores")

        from src.teaching import learner

        profile = learner.update_profile("tester", event)
        assert profile["capability_means"]["subsumption"] > 0
        _ok("learner: profile updated")

        from src.teaching.report import build_report

        report = build_report("tester")
        assert len(report["capability_radar"]) == 8
        _ok("report: radar + gaps + recommendations")


def main() -> int:
    print("=" * 60)
    print("  teaching module offline tests")
    print("=" * 60)
    test_rubrics()
    test_corpus()
    test_citation_check()
    test_transcript_and_scorer()
    print("=" * 60)
    print("  ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
