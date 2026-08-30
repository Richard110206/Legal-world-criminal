from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

INSTRUCTIONAL_ORDER = {
    "罪刑法定原则": 1, "犯罪概念与但书": 2, "故意、过失与意外事件": 3,
    "刑事责任年龄": 4, "正当防卫与防卫过当": 5, "紧急避险": 6,
    "犯罪预备、未遂与中止": 7, "共同犯罪与主从犯": 8, "刑罚种类": 9,
    "抢劫罪的基本构成": 10,
}


def read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_evidence(history: list[dict[str, Any]], item_to_knowledge: dict[str, str]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = defaultdict(lambda: {"events": 0, "correct": 0, "items": set()})
    for event in history:
        item_id = str(event["item_id"])
        knowledge_id = item_to_knowledge.get(item_id)
        if knowledge_id is None:
            continue
        rows[knowledge_id]["events"] += 1
        rows[knowledge_id]["correct"] += int(event["correct"])
        rows[knowledge_id]["items"].add(item_id)
    output = {}
    for knowledge_id, row in rows.items():
        event_count, item_count = int(row["events"]), len(row["items"])
        output[knowledge_id] = {
            "event_count": event_count,
            "item_count": item_count,
            "posterior_mean": (int(row["correct"]) + 1.0) / (event_count + 2.0),
            "evidence_status": "provisional" if event_count >= 3 and item_count >= 2 else "insufficient_evidence",
        }
    return output


def priority(name: str, evidence: dict[str, Any] | None) -> tuple[float, str]:
    order_penalty = INSTRUCTIONAL_ORDER.get(name, 99) * 3.0
    if not evidence:
        return 100.0 - order_penalty, "no_evidence_collect_diagnostic"
    if evidence["evidence_status"] == "insufficient_evidence":
        return 110.0 - evidence["event_count"] * 4 - order_penalty, "insufficient_evidence_collect_more"
    mastery = float(evidence["posterior_mean"])
    if mastery < 0.65:
        return 130.0 + (0.65 - mastery) * 20 - order_penalty, "provisional_weakness_remediation"
    if mastery < 0.80:
        return 85.0 + (0.80 - mastery) * 10 - order_penalty, "provisional_reinforcement"
    return 35.0 - order_penalty, "provisional_mastered_spaced_review"


REASONS = {
    "no_evidence_collect_diagnostic": "尚无证据，优先采集覆盖性诊断证据",
    "insufficient_evidence_collect_more": "已有证据不足，继续补充不同题型证据",
    "provisional_weakness_remediation": "临时画像显示薄弱，安排补救练习",
    "provisional_reinforcement": "临时画像处于不稳定区间，安排巩固",
    "provisional_mastered_spaced_review": "临时画像较高，仅安排低优先级间隔复习",
}


def plan_path(approved: list[dict[str, Any]], q_edges: list[dict[str, Any]], nodes: list[dict[str, Any]], history: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    item_records = {row["candidate_id"]: row for row in approved}
    item_to_knowledge = {str(row["item_id"]): str(row["knowledge_id"]) for row in q_edges}
    knowledge_by_id = {str(row["knowledge_id"]): row for row in nodes}
    items_by_knowledge: dict[str, list[str]] = defaultdict(list)
    for item_id, knowledge_id in item_to_knowledge.items():
        if item_id in item_records:
            items_by_knowledge[knowledge_id].append(item_id)
    evidence = build_evidence(history, item_to_knowledge)
    attempted = {str(row["item_id"]) for row in history}
    candidates, selected = set(item_records) - attempted, []
    selected_by_knowledge: dict[str, int] = defaultdict(int)
    while candidates and len(selected) < limit:
        scored = []
        for item_id in candidates:
            item = item_records[item_id]["item"]
            knowledge_id = item_to_knowledge[item_id]
            name = knowledge_by_id[knowledge_id]["canonical_name"]
            base, reason_code = priority(name, evidence.get(knowledge_id))
            score = base - selected_by_knowledge[knowledge_id] * 35
            score += 3 if item.get("cognitive_dimension") == "应用" and selected_by_knowledge[knowledge_id] else 0
            score -= abs(int(item.get("difficulty", 2)) - 2) * 1.5
            scored.append((score, -INSTRUCTIONAL_ORDER.get(name, 99), item_id, reason_code))
        score, _, item_id, reason_code = max(scored)
        candidates.remove(item_id)
        item, knowledge_id = item_records[item_id]["item"], item_to_knowledge[item_id]
        selected_by_knowledge[knowledge_id] += 1
        selected.append({
            "rank": len(selected) + 1, "item_id": item_id, "knowledge_id": knowledge_id,
            "knowledge_name": knowledge_by_id[knowledge_id]["canonical_name"], "stem": item["stem"],
            "options": item["options"], "cognitive_dimension": item["cognitive_dimension"],
            "difficulty": item["difficulty"], "reason_code": reason_code, "reason": REASONS[reason_code],
            "score": round(score, 4), "answer_included": False,
        })
    gaps = []
    ordered_nodes = sorted(knowledge_by_id.items(), key=lambda pair: INSTRUCTIONAL_ORDER.get(pair[1]["canonical_name"], 99))
    for knowledge_id, node in ordered_nodes:
        row = evidence.get(knowledge_id, {"event_count": 0, "item_count": 0, "posterior_mean": 0.5, "evidence_status": "insufficient_evidence"})
        available = len(items_by_knowledge.get(knowledge_id, []))
        unattempted = len(set(items_by_knowledge.get(knowledge_id, [])) - attempted)
        if unattempted == 0 and row["evidence_status"] == "insufficient_evidence":
            actionability = "content_gap_blocks_additional_diagnosis"
        elif unattempted == 0 and row["evidence_status"] == "provisional" and float(row["posterior_mean"]) < 0.65:
            actionability = "content_gap_blocks_remediation"
        else:
            actionability = "actionable_with_current_bank"
        gaps.append({"knowledge_id": knowledge_id, "knowledge_name": node["canonical_name"], "available_approved_items": available, "unattempted_approved_items": unattempted, "items_needed_for_three_item_bank": max(0, 3 - available), "actionability": actionability, **row})
    return {
        "schema_version": "edubrain-cold-start-path-v1.0", "generated_at": datetime.now(UTC).isoformat(),
        "policy": "insufficient evidence first, then provisional weakness; no answer leakage",
        "recommendations": selected, "knowledge_evidence": gaps,
        "warnings": ["推荐不能证明学习增益", "生成题尚无实测难度和区分度", "学习者输出不包含答案与解析"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved-items", type=Path, required=True)
    parser.add_argument("--q-matrix", type=Path, required=True)
    parser.add_argument("--knowledge-nodes", type=Path, required=True)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--student-id", default="anonymous-new-student")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    history = [row for row in read_jsonl(args.history) if str(row.get("student_id")) == args.student_id]
    result = plan_path(read_jsonl(args.approved_items), read_jsonl(args.q_matrix), read_jsonl(args.knowledge_nodes), history, args.limit)
    result["student_id"] = args.student_id
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"student_id": args.student_id, "recommendation_count": len(result["recommendations"]), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
