"""Cold-start knowledge points (Q-matrix) for the teaching module.

Collects:
  1. knowledge_points from `dataset/quiz_bank.json` (deduped, all 92 questions)
  2. charge / case_cause values from `dataset/criminal_case_dataset.json`

Writes:
    backend/src/teaching/knowledge_points.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_QUIZ_PATH = PROJECT_ROOT / "dataset" / "quiz_bank.json"
DEFAULT_CASES_PATH = PROJECT_ROOT / "dataset" / "criminal_case_dataset.json"
DEFAULT_OUTPUT_PATH = BACKEND_ROOT / "src" / "teaching" / "knowledge_points.json"


def _normalize_kp(value: str) -> str:
    return str(value or "").strip()


def build_knowledge_points(quiz_path: Path, cases_path: Path) -> dict:
    kp_counter: Counter = Counter()
    charge_counter: Counter = Counter()
    cause_counter: Counter = Counter()

    if quiz_path.exists():
        quiz = json.loads(quiz_path.read_text(encoding="utf-8"))
        for item in quiz if isinstance(quiz, list) else []:
            for kp in item.get("knowledge_points") or []:
                normalized = _normalize_kp(kp)
                if normalized:
                    kp_counter[normalized] += 1

    if cases_path.exists():
        cases = json.loads(cases_path.read_text(encoding="utf-8"))
        case_list = cases if isinstance(cases, list) else list(cases.values())
        for case in case_list:
            info = case.get("extracted_info") or {}
            charge = _normalize_kp(info.get("charge"))
            cause = _normalize_kp(info.get("case_cause"))
            if charge:
                charge_counter[charge] += 1
            if cause:
                cause_counter[cause] += 1

    return {
        "schema_version": "knowledge-points-v1",
        "knowledge_points": [
            {"name": name, "frequency": count}
            for name, count in kp_counter.most_common()
        ],
        "charges": [
            {"name": name, "case_count": count}
            for name, count in charge_counter.most_common()
        ],
        "case_causes": [
            {"name": name, "case_count": count}
            for name, count in cause_counter.most_common()
        ],
        "stats": {
            "knowledge_points": len(kp_counter),
            "charges": len(charge_counter),
            "case_causes": len(cause_counter),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiz-path", default=str(DEFAULT_QUIZ_PATH))
    parser.add_argument("--cases-path", default=str(DEFAULT_CASES_PATH))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    args = parser.parse_args()

    payload = build_knowledge_points(Path(args.quiz_path), Path(args.cases_path))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["stats"], ensure_ascii=False))
    print(f"written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
