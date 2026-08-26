"""LLM-as-judge teaching scorer → LearningEvent.

Flow (never blocks the simulation; all failures are logged only):
  1. assemble scoring input (transcript)
  2. deterministic local citation verification across student utterances
  3. call DeepSeek judge (temperature 0.2) with retries
  4. parse & normalize → LearningEvent
  5. persist to `case_output_dir/teaching/{stage}_learning_event.json`
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

from . import citation_alignment, citation_check, deterministic, rubrics, transcript  # noqa: E402
from .deterministic import merge_deterministic_score  # noqa: E402
from .rubrics import (  # noqa: E402
    build_judge_eval_prompt,
    build_judge_system_prompt,
    stage_capability_weights,
)

JUDGE_MAX_ATTEMPTS = 3
JUDGE_TEMPERATURE = 0.2
JUDGE_MAX_TOKENS = 4096
LEARNING_EVENT_SCHEMA = "learning-event-v1"


class TeachingScorer:
    """LLM-as-judge scorer producing structured LearningEvents."""

    def __init__(self, judge_model_type: str | None = None, judge_factory: Callable | None = None):
        self._judge_model_type = judge_model_type
        self._judge_factory = judge_factory
        self._judge_agent_cache: dict[str, Any] = {}

    # ── judge plumbing (mirrors eval_pipeline) ──────────────────────
    def _create_judge_agent(self, system_prompt: str) -> Any:
        if self._judge_factory is not None:
            return self._judge_factory(system_prompt)

        from camel.agents import ChatAgent
        from camel.models import ModelFactory
        from camel.types import ModelPlatformType

        from ..utils.model_config import build_runtime_openai_chat_config, resolve_openai_chat_model

        judge_model = resolve_openai_chat_model(explicit_model=self._judge_model_type)
        model = ModelFactory.create(
            model_platform=ModelPlatformType.OPENAI,
            model_type=judge_model,
            model_config_dict=build_runtime_openai_chat_config(
                model_name=judge_model,
                temperature=JUDGE_TEMPERATURE,
                max_tokens=JUDGE_MAX_TOKENS,
            ),
        )
        return ChatAgent(system_message=system_prompt, model=model)

    def _judge_call(self, agent: Any, prompt: str) -> str:
        from camel.messages import BaseMessage

        # camel ChatAgent.step accumulates conversation memory; agents are
        # cached per system_prompt and reused across retries, so reset to keep
        # each judging call independent (same defense as citation_alignment).
        if hasattr(agent, "reset"):
            agent.reset()
        user_message = BaseMessage.make_user_message(role_name="user", content=prompt)
        response = agent.step(user_message)
        return response.msgs[0].content

    # ── parsing ─────────────────────────────────────────────────────
    @staticmethod
    def _extract_json_payload(response: str) -> dict[str, Any] | None:
        text = str(response or "").strip()
        if not text:
            return None
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def _normalize_capability_scores(
        raw: dict[str, Any],
        stage: str,
    ) -> dict[str, Any]:
        weights = stage_capability_weights(stage)
        scores: dict[str, Any] = {}
        for code, weight in weights.items():
            entry = raw.get(code) if isinstance(raw, dict) else None
            if not isinstance(entry, dict):
                entry = {}
            try:
                score = max(0, min(10, int(entry.get("score"))))
            except (TypeError, ValueError):
                # judge omitted this capability → abstain (missing), NOT 0:
                # "not judged" must never drag the learner profile down
                scores[code] = {
                    "score": None,
                    "raw": None,
                    "weight": weight,
                    "source": "missing",
                    "rationale": str(entry.get("rationale") or "").strip(),
                    "evidence_quote": str(entry.get("evidence_quote") or "").strip(),
                }
                continue
            scores[code] = {
                "score": round(score / 10.0, 3),
                "raw": score,
                "weight": weight,
                "source": "judge",
                "rationale": str(entry.get("rationale") or "").strip(),
                "evidence_quote": str(entry.get("evidence_quote") or "").strip(),
            }
        return scores

    # ── main scoring ────────────────────────────────────────────────
    def score_stage(
        self,
        *,
        case_id: str,
        stage: str,
        case_output_dir: Path,
        student_id: str = "",
        run_async: bool = False,
    ) -> dict[str, Any] | None:
        """Score one stage; returns the LearningEvent dict (or None on failure)."""
        if run_async:
            thread = threading.Thread(
                target=self._score_stage_safe,
                kwargs={
                    "case_id": case_id,
                    "stage": stage,
                    "case_output_dir": case_output_dir,
                    "student_id": student_id,
                },
                daemon=True,
            )
            thread.start()
            return None
        return self._score_stage_safe(
            case_id=case_id,
            stage=stage,
            case_output_dir=case_output_dir,
            student_id=student_id,
        )

    def _score_stage_safe(self, **kwargs: Any) -> dict[str, Any] | None:
        try:
            return self._score_stage_impl(**kwargs)
        except Exception as exc:
            logger.exception("[TeachingScorer] scoring failed for %s/%s: %s",
                             kwargs.get("case_id"), kwargs.get("stage"), exc)
            return None

    def _score_stage_impl(
        self,
        *,
        case_id: str,
        stage: str,
        case_output_dir: Path,
        student_id: str = "",
    ) -> dict[str, Any] | None:
        stage = str(stage or "").strip().upper()
        case_output_dir = Path(case_output_dir)

        scoring_input = transcript.build_scoring_input(case_id, stage, case_output_dir)
        utterances = scoring_input.get("utterances") or []
        if not utterances:
            logger.info("[TeachingScorer] no student utterances for %s/%s; skipped", case_id, stage)
            return None

        # deterministic citation verification
        utterance_texts = [str(item.get("text") or "") for item in utterances]
        law_citations = citation_check.collect_law_citations(utterance_texts)
        # attach verified citation info into the judge transcript
        scoring_input["law_citations_precheck"] = [
            {
                "citation": item.get("citation"),
                "status": item.get("status"),
                "issue": item.get("issue", ""),
            }
            for item in law_citations
        ]

        # NLI citation-sentence alignment (dual-layer: local model + LLM judge)
        alignment_result: dict[str, Any] = {"items": [], "summary": {}}
        try:
            alignment_result = citation_alignment.verify_alignment(
                utterance_texts,
                judge_client=self._create_judge_agent(
                    citation_alignment.JUDGE_SYSTEM_PROMPT
                ),
            )
        except Exception as exc:
            logger.warning("[TeachingScorer] citation alignment failed (non-blocking): %s", exc)
        if alignment_result.get("items"):
            scoring_input["citation_alignment"] = alignment_result["items"]
            scoring_input["alignment_summary"] = alignment_result["summary"]

        system_prompt = build_judge_system_prompt(stage)
        judge_prompt = build_judge_eval_prompt(
            stage,
            scoring_input,
            scoring_input.get("gold"),
        )

        payload = None
        last_error = ""
        for attempt in range(1, JUDGE_MAX_ATTEMPTS + 1):
            try:
                agent = self._judge_agent_cache.get(system_prompt)
                if agent is None:
                    agent = self._create_judge_agent(system_prompt)
                    self._judge_agent_cache[system_prompt] = agent
                response = self._judge_call(agent, judge_prompt)
                payload = self._extract_json_payload(response)
                if payload:
                    break
                last_error = f"attempt {attempt}: could not parse judge JSON"
                logger.warning("[TeachingScorer] %s", last_error)
                judge_prompt += "\n只返回合法 JSON，不要附加任何说明。"
            except Exception as exc:
                last_error = f"attempt {attempt}: {exc}"
                logger.warning("[TeachingScorer] judge call failed: %s", last_error)

        if payload is None:
            logger.error("[TeachingScorer] judge failed for %s/%s: %s", case_id, stage, last_error)
            return None

        event = self._build_learning_event(
            case_id=case_id,
            stage=stage,
            charge=scoring_input.get("charge") or "",
            student_id=student_id,
            payload=payload,
            law_citations=law_citations,
            gold_incomplete=bool(scoring_input.get("gold_incomplete")),
            alignment_result=alignment_result,
            utterance_texts=utterance_texts,
        )
        self._persist(case_id, stage, case_output_dir, event)

        # 画像累计 + 技能卡沉淀（失败不影响已落盘的 LearningEvent）
        try:
            from . import learner

            learner.update_profile(event.get("student_id") or "anonymous", event)
        except Exception as exc:
            logger.warning("[TeachingScorer] profile update failed for %s/%s: %s", case_id, stage, exc)
        try:
            from . import skill_card

            skill_card.update_skill_cards(event.get("student_id") or "anonymous", event)
        except Exception as exc:
            logger.warning("[TeachingScorer] skill card update failed for %s/%s: %s", case_id, stage, exc)

        return event

    def _build_learning_event(
        self,
        *,
        case_id: str,
        stage: str,
        charge: str,
        student_id: str,
        payload: dict[str, Any],
        law_citations: list[dict[str, Any]],
        gold_incomplete: bool,
        alignment_result: dict[str, Any] | None = None,
        utterance_texts: list[str] | None = None,
    ) -> dict[str, Any]:
        capability_scores = self._normalize_capability_scores(
            payload.get("capability_scores") or {}, stage
        )
        self._verify_evidence_quotes(capability_scores, utterance_texts or [])

        # deterministic rule_retrieval overrides the judge's subjective score
        # when citation/NLI evidence exists (abstains on zero-valid citations)
        det_entry = deterministic.score_rule_retrieval(law_citations, alignment_result)
        if det_entry is not None:
            merge_deterministic_score(capability_scores, det_entry)

        # merge NLI-derived error tags (contradicted citations) with judge tags
        error_tags = [str(tag) for tag in (payload.get("error_tags") or [])]
        if alignment_result:
            for tag in citation_alignment.error_tags_from_alignment(alignment_result):
                if tag not in error_tags:
                    error_tags.append(tag)

        alignment_items = (alignment_result or {}).get("items") or []
        alignment_summary = (alignment_result or {}).get("summary") or {}

        return {
            "event_id": f"evt_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{case_id}_{stage}",
            "schema_version": LEARNING_EVENT_SCHEMA,
            "student_id": student_id or "anonymous",
            "case_id": case_id,
            "charge": charge,
            "stage": stage,
            "gold_incomplete": gold_incomplete,
            "capability_scores": capability_scores,
            "subsumption_table": payload.get("subsumption_table") or [],
            "knowledge_verdicts": payload.get("knowledge_verdicts") or [],
            "error_tags": error_tags,
            "knowledge_gaps": [str(gap) for gap in (payload.get("knowledge_gaps") or [])],
            "citation_alignment": alignment_items,
            "alignment_summary": alignment_summary,
            "law_citations": [
                {
                    "citation": item.get("citation"),
                    "title": item.get("title"),
                    "article_ref": item.get("article_ref"),
                    "status": item.get("status"),
                    "content": item.get("content", ""),
                    "issue": item.get("issue", ""),
                }
                for item in law_citations
            ],
            "overall_feedback": str(payload.get("overall_feedback") or "").strip(),
            "scored_at": datetime.now().isoformat(timespec="seconds"),
        }

    @staticmethod
    def _verify_evidence_quotes(
        capability_scores: dict[str, Any],
        utterance_texts: list[str],
    ) -> None:
        """Flag judge-claimed evidence quotes that don't appear in the transcript.

        LLM judges sometimes paraphrase or fabricate quotes; an unverifiable
        quote is a signal the rationale may not be grounded. Flag only — the
        score stands, the reader sees the caveat.
        """
        if not utterance_texts:
            return
        haystack = " ".join(utterance_texts)
        # normalize whitespace so line breaks / duplicated spaces don't break matching
        haystack_norm = re.sub(r"\s+", "", haystack)
        for entry in capability_scores.values():
            if not isinstance(entry, dict):
                continue
            quote = str(entry.get("evidence_quote") or "").strip()
            if not quote:
                continue  # no claim → nothing to verify
            if entry.get("source") == "deterministic":
                continue  # deterministic evidence lists citations, not quotes
            needle = re.sub(r"\s+", "", quote)
            if needle and needle not in haystack_norm:
                entry["unverified"] = True

    @staticmethod
    def _persist(case_id: str, stage: str, case_output_dir: Path, event: dict[str, Any]) -> Path:
        teaching_dir = Path(case_output_dir) / "teaching"
        teaching_dir.mkdir(parents=True, exist_ok=True)
        output_path = teaching_dir / f"{stage}_learning_event.json"
        output_path.write_text(
            json.dumps(event, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("[TeachingScorer] wrote LearningEvent for %s/%s -> %s", case_id, stage, output_path)
        return output_path


def score_stage_sync(
    *,
    case_id: str,
    stage: str,
    case_output_dir: Path,
    student_id: str = "",
) -> dict[str, Any] | None:
    """Module-level convenience wrapper (loads .env so judge model config resolves)."""
    load_dotenv()
    return TeachingScorer().score_stage(
        case_id=case_id,
        stage=stage,
        case_output_dir=case_output_dir,
        student_id=student_id,
    )


__all__ = ["TeachingScorer", "score_stage_sync"]
