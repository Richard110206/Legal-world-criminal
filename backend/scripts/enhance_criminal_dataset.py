"""刑事案件数据集增量增强 — 按刑法流程各阶段补齐消费字段。

在 parse_criminal_txt.py 生成的 criminal_case_dataset.json 基础上做增量补充
（不覆盖已有非空字段），新增以下刑事流程专属字段：

  investigation_stage:   侦查阶段（INV）事实底料
      - suspect_identity（嫌疑人基本情况）
      - suspected_charge（涉嫌罪名）
      - custody_status / detention_date / bail_status（强制措施现状）
      - key_facts_for_bail（取保候审申请可用事实：初犯/疾病/认罪/赔偿等）
      - lawyer_actions（律师可依法开展的工作清单）
  prosecution_stage:     审查起诉阶段（PR）事实底料
      - indictment_summary（指控事实摘要）
      - evidence_catalog（证据目录：从"证据"段提取）
      - sentencing_factors（量刑情节，沿用 parse 阶段结果）
      - defense_opportunities（辩点清单：程序/证据/量刑三维度）
      - non_prosecution_arguments（不起诉/罪轻辩护空间）
  defense_stage:         辩护词起草（DS）事实底料
      - charge（罪名）
      - facts_agreed（无争议事实）
      - facts_disputed（有争议事实）
      - mitigating_factors（从宽情节清单）
      - defense_positions（辩护意见骨架：无罪/罪轻/程序辩护）
  trial_stage:           刑事一审（CR）事实底料
      - prosecution_claims（公诉机关指控）
      - contested_issues（争议焦点）
      - evidence_confrontation_points（质证要点）
      - reference_judgment（参考判项——来自数据集真实判决主文）
  appeal_stage:          刑事二审（CRA）事实底料
      - has_appeal / appeal_reasons（上诉事实与理由）
      - first_verdict_summary（一审判决主文摘要）
      - second_instance_grounds（二审审理要点）

用法:
    cd backend
    python scripts/enhance_criminal_dataset.py [--dataset PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = REPO_ROOT / "dataset" / "criminal_case_dataset.json"

MAX_TEXT = 2000

MITIGATING_MAP = {
    "自首": "主动投案并如实供述，构成自首",
    "坦白": "到案后如实供述自己罪行，构成坦白",
    "认罪认罚": "自愿认罪认罚，签署具结书",
    "赔偿谅解": "赔偿被害人损失并取得谅解",
    "从犯": "在共同犯罪中起次要作用，系从犯",
    "未遂": "犯罪未得逞，系未遂",
    "中止": "自动放弃犯罪或有效防止结果发生，系中止",
    "未成年人": "犯罪时未满十八周岁",
    "限制刑事责任能力": "系限制刑事责任能力人",
}

AGGRAVATING_MAP = {
    "累犯": "刑满释放后五年内再犯，系累犯，应当从重处罚",
    "前科": "有前科劣迹，酌情从重",
    "主犯": "在共同犯罪中起主要作用，系主犯",
}

LAWYER_INVESTIGATION_ACTIONS = [
    "向侦查机关了解嫌疑人涉嫌的罪名和案件有关情况",
    "会见在押的犯罪嫌疑人，了解其身体状况和案件经过",
    "为犯罪嫌疑人提供法律咨询，告知其诉讼权利",
    "代理申诉、控告",
    "申请变更强制措施为取保候审",
    "向侦查机关提交书面辩护意见",
]

EVIDENCE_SECTION_RE = re.compile(
    r"(上述事实[^。]*?有[^。]*?证据[^。]*。)|(证据等?[:：])"
)
EVIDENCE_ITEM_SPLIT_RE = re.compile(r"[、，,；;]\s*")
EVIDENCE_KIND_RE = re.compile(
    r"(证人证言|物证|书证|被害人陈述|被告人供述|勘验[^，。]{0,10}|检查[^，。]{0,10}|辨认笔录|鉴定意见|鉴定书|视听资料|电子数据|抓获经过|到案经过|破案经过|情况说明|户口信息|前科材料)"
)


def _norm(raw: Any) -> str:
    return re.sub(r"\s+", " ", str(raw or "")).strip()


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    text = _norm(text)
    return text[:limit]


def _non_empty(*values: str) -> str:
    for v in values:
        if _norm(v):
            return _norm(v)
    return ""


# ── INV: 侦查阶段 ────────────────────────────────────────────
def build_investigation_stage(info: dict) -> dict:
    fi = info.get("first_instance", {}) or {}
    compulsory = info.get("compulsory_measures", {}) or {}
    defendant = (info.get("party_info", {}) or {}).get("defendant", {}) or {}
    factors = list(info.get("sentencing_factors", []) or [])

    bail_facts: list[str] = []
    if "自首" in factors:
        bail_facts.append("有自首情节，社会危险性较低")
    if "坦白" in factors or "认罪认罚" in factors:
        bail_facts.append("到案后如实供述/认罪认罚，配合调查")
    if "赔偿谅解" in factors:
        bail_facts.append("已赔偿被害人损失并取得谅解")
    if "未遂" in factors or "中止" in factors:
        bail_facts.append("犯罪未遂/中止，实际危害有限")
    if "从犯" in factors:
        bail_facts.append("系从犯，在共同犯罪中作用次要")
    if "未成年人" in factors:
        bail_facts.append("系未成年人，应当特别程序保护")
    if not bail_facts:
        bail_facts.append("涉嫌罪名非暴力恶性犯罪，可评估社会危险性后申请取保候审")

    if compulsory.get("bail"):
        custody = "取保候审（非在押）"
    elif _norm(compulsory.get("custody_status")) == "在押":
        custody = "在押"
    elif compulsory.get("detention"):
        custody = "曾刑事拘留，现况以在案材料为准"
    else:
        custody = "未知"

    return {
        "suspect_identity": _clip(defendant.get("raw_description", ""), 500),
        "suspected_charge": _norm(info.get("charge")) or _norm(info.get("case_cause")),
        "custody_status": custody,
        "detention_date": _norm(compulsory.get("detention_date")),
        "bail_status": "已取保候审" if compulsory.get("bail") else "未取保",
        "key_facts_for_bail": bail_facts,
        "lawyer_actions": list(LAWYER_INVESTIGATION_ACTIONS),
        "case_summary": _clip(
            _non_empty(
                (info.get("prosecution", {}) or {}).get("accusation"),
                fi.get("court_finding"),
                info.get("case_background"),
            ),
            1200,
        ),
    }


# ── PR: 审查起诉阶段 ─────────────────────────────────────────
def _extract_evidence_catalog(info: dict) -> list[str]:
    text = "\n".join(
        _norm(v)
        for v in [
            (info.get("first_instance", {}) or {}).get("court_finding", ""),
            info.get("procedure_history", ""),
            info.get("case_background", ""),
        ]
        if isinstance(v, str)
    )
    items: list[str] = []
    m = EVIDENCE_SECTION_RE.search(text)
    if m:
        segment = text[m.start() : m.end() + 260]
        for kind in EVIDENCE_KIND_RE.findall(segment):
            if kind not in items:
                items.append(kind)
    if not items:
        for kind in EVIDENCE_KIND_RE.findall(text[:1500]):
            if kind not in items and len(items) < 6:
                items.append(kind)
    if not items:
        items = ["被告人供述", "被害人陈述", "证人证言", "物证", "书证", "鉴定意见"]
    return items[:8]


def build_prosecution_stage(info: dict) -> dict:
    fi = info.get("first_instance", {}) or {}
    prosecution = info.get("prosecution", {}) or {}
    factors = list(info.get("sentencing_factors", []) or [])
    factors_text = "、".join(factors) if factors else "无明显量刑情节"

    mitigating = [MITIGATING_MAP[f] for f in factors if f in MITIGATING_MAP]
    aggravating = [AGGRAVATING_MAP[f] for f in factors if f in AGGRAVATING_MAP]

    defense_opp: list[str] = []
    if mitigating:
        defense_opp.append("量刑辩护：充分运用从宽情节（" + "；".join(mitigating) + "）")
    if (fi.get("court_opinion") or "").find("证据") >= 0:
        defense_opp.append("证据辩护：核查证据链完整性、取证程序合法性")
    if not (fi.get("main_sentence") or "").strip():
        defense_opp.append("事实辩护：核对指控事实与在案证据的对应关系")
    if not defense_opp:
        defense_opp.append("程序辩护与量刑辩护并行：确保诉讼权利落实，争取从宽处理")

    non_pros: list[str] = []
    if "未遂" in factors or "中止" in factors:
        non_pros.append("犯罪形态辩点（未遂/中止），可主张情节显著轻微")
    if "从犯" in factors:
        non_pros.append("从犯地位辩点，可主张作用较小、情节轻微")
    if "自首" in factors and "认罪认罚" in factors:
        non_pros.append("自首+认罪认罚双重从宽，可争取不起诉或轻缓量刑建议")
    if not non_pros:
        non_pros.append("以罪轻辩护为主，不起诉空间有限")

    return {
        "indictment_summary": _clip(_non_empty(prosecution.get("accusation"), fi.get("court_finding")), 1500),
        "evidence_catalog": _extract_evidence_catalog(info),
        "sentencing_factors": factors,
        "defense_opportunities": defense_opp,
        "non_prosecution_arguments": non_pros,
        "mitigating_factors": mitigating,
        "aggravating_factors": aggravating,
        "factors_overview": factors_text,
    }


# ── DS: 辩护词起草 ───────────────────────────────────────────
def build_defense_stage(info: dict) -> dict:
    fi = info.get("first_instance", {}) or {}
    prosecution = info.get("prosecution", {}) or {}
    factors = list(info.get("sentencing_factors", []) or [])
    charge = _norm(info.get("charge")) or _norm(info.get("case_cause"))

    facts_agreed: list[str] = []
    accusation = _norm(prosecution.get("accusation"))
    if accusation:
        facts_agreed.append("指控的基本犯罪事实（被告人认罪部分）")
    if "认罪认罚" in factors or "坦白" in factors:
        facts_agreed.append("被告人到案后如实供述的主要罪行")
    if "赔偿谅解" in factors:
        facts_agreed.append("已赔偿被害人经济损失并取得谅解的事实")
    if not facts_agreed:
        facts_agreed.append("指控事实的主体部分（以在案证据为限）")

    facts_disputed: list[str] = []
    hint = _norm(info.get("defense_hint"))
    if hint:
        facts_disputed.append(hint)
    if "主犯" in factors:
        facts_disputed.append("在共同犯罪中的地位、作用存在争议")
    if not (fi.get("main_sentence") or "").strip():
        facts_disputed.append("部分事实细节与证据的对应关系待核实")
    if not facts_disputed:
        facts_disputed.append("主要围绕量刑情节展开，事实部分争议有限")

    mitigating = [MITIGATING_MAP[f] for f in factors if f in MITIGATING_MAP]

    positions: list[str] = []
    has_guilt_issue = bool(hint) and bool(re.search(r"不构成|无罪|此罪彼罪|罪名异议", hint))
    if has_guilt_issue:
        positions.append("无罪/罪名辩护：就行为定性（此罪与彼罪）提出异议")
    positions.append("罪轻辩护：围绕从宽情节请求从轻、减轻处罚")
    if mitigating:
        positions.append("量刑辩护：重点论证 " + "；".join(mitigating[:3]))
    positions.append("程序辩护：核查侦查、审查起诉程序合法性，保障诉讼权利")

    return {
        "charge": charge,
        "facts_agreed": facts_agreed,
        "facts_disputed": facts_disputed,
        "mitigating_factors": mitigating or ["坦白、认罪悔罪等酌定从宽情节"],
        "defense_positions": positions,
        "reference_judgment": _clip(fi.get("main_sentence", ""), 600),
    }


# ── CR: 刑事一审庭审 ─────────────────────────────────────────
def build_trial_stage(info: dict) -> dict:
    fi = info.get("first_instance", {}) or {}
    prosecution = info.get("prosecution", {}) or {}
    factors = list(info.get("sentencing_factors", []) or [])

    contested: list[str] = []
    if _norm(info.get("defense_hint")):
        contested.append("被告人及其辩护人对指控事实的异议")
    contested.append("量刑情节的认定（" + ("、".join(factors) if factors else "法定/酌定情节") + "）")
    if "主犯" in factors or "从犯" in factors:
        contested.append("共同犯罪中的地位与作用")
    contested.append("证据的真实性、合法性、关联性")

    confrontation: list[str] = []
    catalog = build_prosecution_stage(info)["evidence_catalog"]
    for kind in catalog[:4]:
        confrontation.append(f"对{kind}的取证程序与证明力发表质证意见")
    if not confrontation:
        confrontation = ["对指控证据的三性（真实性、合法性、关联性）发表质证意见"]

    return {
        "prosecution_claims": _clip(_non_empty(prosecution.get("accusation"), fi.get("court_finding")), 1500),
        "contested_issues": contested,
        "evidence_confrontation_points": confrontation,
        "evidence_catalog": catalog,
        "reference_judgment": _clip(fi.get("main_sentence", ""), 600),
        "sentencing_factors": factors,
    }


# ── CRA: 刑事二审 ────────────────────────────────────────────
def build_appeal_stage(info: dict) -> dict:
    fi = info.get("first_instance", {}) or {}
    si = info.get("second_instance", {}) or {}
    prosecution = info.get("prosecution", {}) or {}
    factors = list(info.get("sentencing_factors", []) or [])

    grounds: list[str] = []
    reason = _norm(si.get("appeal_reason"))
    if reason:
        grounds.append(f"上诉理由：{reason[:300]}")
    if "自首" in factors or "坦白" in factors or "认罪认罚" in factors:
        grounds.append("量刑畸重：一审判决未充分评价从宽情节")
    if "主犯" in factors or "从犯" in factors:
        grounds.append("地位作用认定不当：共同犯罪中的作用划分")
    if not grounds:
        grounds.append("就量刑适当性与事实认定提出上诉理由")

    return {
        "has_appeal": bool(si.get("has_appeal")),
        "appeal_reasons": grounds,
        "first_verdict_summary": _clip(fi.get("main_sentence", ""), 600),
        "first_court_opinion": _clip(fi.get("court_opinion", ""), 1000),
        "second_instance_grounds": [
            "全面审查一审判决认定的事实与适用法律",
            "审查上诉理由是否成立",
            _clip(_non_empty(si.get("court_opinion"), prosecution.get("prosecution_opinion")), 500),
        ],
        "reference_judgment": _clip(
            _non_empty(
                (si.get("final_judgment", {}) or {}).get("judgment_result", [""])[0] if (si.get("final_judgment", {}) or {}).get("judgment_result") else "",
                fi.get("main_sentence", ""),
            ),
            600,
        ),
    }


# ── 主流程 ───────────────────────────────────────────────────
def enhance_case(info: dict) -> dict[str, list[str]]:
    """对单个案件的 extracted_info 做增量增强，返回新增字段报告。"""
    added: list[str] = []
    stages = {
        "investigation_stage": build_investigation_stage(info),
        "prosecution_stage": build_prosecution_stage(info),
        "defense_stage": build_defense_stage(info),
        "trial_stage": build_trial_stage(info),
        "appeal_stage": build_appeal_stage(info),
    }
    for key, value in stages.items():
        if not info.get(key):
            info[key] = value
            added.append(key)
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dataset_path: Path = args.dataset.resolve()
    if not dataset_path.exists():
        print(f"[ERROR] dataset not found: {dataset_path}", file=sys.stderr)
        return 1

    with dataset_path.open("r", encoding="utf-8") as fh:
        cases = json.load(fh)
    if not isinstance(cases, list):
        print("[ERROR] expected list at top level", file=sys.stderr)
        return 1

    stats: dict[str, int] = {}
    for case in cases:
        info = case.get("extracted_info") or {}
        for key in enhance_case(info):
            stats[key] = stats.get(key, 0) + 1
        case["extracted_info"] = info

    print(f"案件总数: {len(cases)}")
    for key, count in sorted(stats.items()):
        print(f"  +{key}: {count} 案新增")

    if args.dry_run:
        print("(dry-run，未写盘)")
        return 0

    with dataset_path.open("w", encoding="utf-8") as fh:
        json.dump(cases, fh, ensure_ascii=False, indent=2)
    print(f"已写回 {dataset_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
