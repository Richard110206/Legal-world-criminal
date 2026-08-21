"""刑事案件txt数据集 → criminal_case_dataset.json 解析器。

输入: 同学整理的txt（按刑法章节组织，约169个案例，三种形态混合）:
  1. 判决书全文式: 被告人：/公诉机关：/审理经过：/本院查明：/本院认为：/裁判结果：
  2. 指导性案例式: 基本案情：/裁判要点：/裁判结果：/裁判理由：/相关法条：
  3. 【】标签式: 【基本案情】【裁判理由】【裁判要旨】【关联索引】

输出: 与民事 light_case_dataset.json 同构的 list[{original_id, extracted_info}]，
      party_info 以 defendant 为主角，追加刑事特有字段。

用法:
    cd backend
    python scripts/parse_criminal_txt.py [--source PATH] [--out PATH] [--max-cases N]
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
DEFAULT_SOURCE = Path(r"E:\大二下活动\【揭榜挂帅】法律一流学科建设\数据集\案例_文本文档格式.txt")
DEFAULT_OUT = REPO_ROOT / "dataset" / "criminal_case_dataset.json"

# ── 结构行正则 ──────────────────────────────────────────────
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十]+章\s*(.+)$")
SUBSECTION_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)+)\s*(.+)$")  # 2.1xxx / 3.1.1xxx
CASE_TITLE_RE = re.compile(r"^([0-9]+)[\.、]\s*(.+)$")
# 小节里的罪名段（如"2.1放火罪、决水罪…"）不能当案例；真实案例标题以"案"结尾/含副标题，
# 且长度有限（证据列举等长文本不是标题）
REAL_CASE_HINT_RE = re.compile(r"案|号[：:]")
MAX_TITLE_LEN = 60
SECTION_HEADER_RE = re.compile(r"^第[一二三四五六七八九十]+节\s*(.+)$")

# 判决书字段标签（行首，中文冒号结尾）
VERDICT_LABELS = [
    "案由", "审理法官", "审理法院", "公诉机关", "公诉机", "公诉人", "被告人",
    "辩护人", "附带民事公益诉讼被告", "原公诉机关暨附带民事公益诉讼起诉人",
    "上诉人", "原审被告人", "辩护人暨诉讼代理人", "审理经过", "检察院指控",
    "公诉机关认为", "被告辩称", "被告人及辩护人", "本院查明", "审理查明",
    "一审法院查明", "检察院认为", "本院认为", "一审法院认为", "一审认为",
    "裁判结果", "判决结果", "裁判理由", "一审判决", "裁判要旨", "基本案情",
    "案情", "法条依据", "法律依据", "依据法条", "相关法条", "评析", "点评",
    "审判", "裁判", "上诉", "抗诉", "再审", "死刑复核", "复核结果",
    "关联索引", "量刑建议", "被告人供述", "被害人", "原告诉称",
]
LABEL_RE = re.compile(r"^([^，。；：()（）\[\]【""'']{2,14})[：:]\s*")

# 【】标签
BRACKET_LABELS = ["基本案情", "裁判理由", "裁判要旨", "关联索引", "评析", "案情", "审判", "裁判", "点评"]
BRACKET_RE = re.compile(r"^【([^】]{2,6})】\s*(.*)$")

CASE_NUMBER_RE = re.compile(r"[（(]([0-9]{4})[）)]([^（）()]{2,18}?)刑(初|终|再|核|抗)[^（）()]*?号")
COURT_RE = re.compile(r"([一-龥]{2,20}?)(人民法院|中级法院)")
SENTENCE_RE = re.compile(
    r"(有期徒刑|无期徒刑|死刑|拘役|管制)[^，。;；]{0,30}?"
    r"(?:，缓刑[^，。;；]{0,12}年?)?"
)
FINE_RE = re.compile(r"罚金[^，。;；]{0,20}")


def _norm_text(raw: str) -> str:
    return re.sub(r"\s+", " ", str(raw or "")).strip()


def _first_match(pattern: re.Pattern, text: str) -> str | None:
    m = pattern.search(text or "")
    return m.group(0) if m else None


# ── 判决主文提取 ────────────────────────────────────────────
def _parse_judgment_items(text: str) -> list[str]:
    """从裁判结果文本中拆分判项。"""
    if not text:
        return []
    body = text.strip()
    body = re.sub(r"^(裁判结果|判决结果)[：:]\s*", "", body)
    items = re.split(r"[；;]\s*(?=[一二三四五六七八九十]{1,2}[、．.]|判处|被告人|责令|没收|驳回|作案工具|继续追缴|附加)", body)
    cleaned = [_norm_text(x) for x in items if _norm_text(x)]
    return cleaned or ([_norm_text(body)] if _norm_text(body) else [])


def _extract_main_sentence(items: list[str]) -> str:
    """主刑判项（含被告人姓名+刑期的那条）。"""
    for item in items:
        if SENTENCE_RE.search(item) and ("犯" in item or "判处" in item):
            return item
    for item in items:
        if SENTENCE_RE.search(item):
            return item
    return items[0] if items else ""


def _extract_prosecution_opinion(facts: dict, accusation_fallback: str = "") -> dict:
    """构造起诉指控（供PR阶段检察官Agent与起诉书起草使用）。"""
    accusation = (
        facts.get("检察院指控") or accusation_fallback
        or facts.get("基本案情") or facts.get("案情") or ""
    )
    opinion = (
        facts.get("公诉机关认为") or facts.get("检察院认为")
        or facts.get("公诉机关指控上述犯罪事实所列举的证据") or ""
    )
    return {"accusation": _norm_text(accusation), "prosecution_opinion": _norm_text(opinion)}


def _extract_defense_hint(facts: dict) -> str:
    """辩护要点线索（喂给辩护律师Agent）。"""
    parts = []
    if facts.get("被告辩称"):
        parts.append(f"被告人辩称：{facts['被告辩称']}")
    if facts.get("辩护人"):
        parts.append(f"辩护人：{facts['辩护人']}")
    return " ".join(parts)


def _extract_compulsory_measure(defendant_raw: str, procedure: str) -> dict:
    """强制措施信息（侦查阶段INV的事实底料）。"""
    info: dict[str, str] = {}
    text = f"{defendant_raw} {procedure}"
    if "刑事拘留" in text or "拘留" in text:
        info["detention"] = "刑事拘留"
    if "逮捕" in text:
        info["arrest"] = "逮捕"
    if "取保候审" in text:
        info["bail"] = "取保候审"
    if "羁押" in text:
        info["custody_status"] = "在押"
    m = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日)[^，。]{0,12}被?刑事拘留", text)
    if m:
        info["detention_date"] = m.group(1)
    return info


def _sentencing_factors(facts: dict, court_opinion: str, defendant_raw: str) -> list[str]:
    """从文书推断量刑情节（坦白/自首/认罪认罚/赔偿谅解/累犯等）。"""
    text = " ".join([str(v) for v in facts.values() if isinstance(v, str)]) + court_opinion + defendant_raw
    factors: list[str] = []
    checks = [
        ("自首", r"自首"),
        ("坦白", r"坦白|如实供述"),
        ("认罪认罚", r"认罪认罚"),
        ("赔偿谅解", r"赔偿[^，。]{0,15}(被害人|经济损失)|取得[^，。]{0,8}谅解|谅解书"),
        ("累犯", r"累犯"),
        ("前科", r"前科|曾因[^，。]{2,20}被[^，。]{0,10}(处罚|判刑|有期徒刑)"),
        ("缓刑", r"缓刑"),
        ("未遂", r"未遂"),
        ("中止", r"中止"),
        ("从犯", r"从犯"),
        ("主犯", r"主犯"),
        ("未成年人", r"未成年"),
        ("限制刑事责任能力", r"限定?刑事责任能力"),
    ]
    for label, pattern in checks:
        if re.search(pattern, text):
            factors.append(label)
    return factors


def _defendant_profile(name: str, raw: str, case_title: str) -> dict:
    """构造被告人 party profile（对齐民事 plaintiff 的画像结构）。"""
    literacy = "medium"
    if re.search(r"智力残疾|精神发育迟滞|文盲|小学文化|文盲或半文盲", raw):
        literacy = "low"
    elif re.search(r"大学|本科|大专|高中|中专", raw):
        literacy = "medium"

    attitude = "medium"
    if re.search(r"认罪认罚|没有异议|不持异议|如实供述", raw):
        attitude = "cooperative"
    elif re.search(r"零口供|否认|拒不认罪", raw + case_title):
        attitude = "defiant"

    return {
        "name": name,
        "type": "自然人",
        "legal_persona_profile": {
            "legal_literacy_level": literacy,
            "information_disclosure_willingness": "high" if attitude == "cooperative" else "medium",
            "emotional_stability": "medium",
            "narrative_proficiency": "medium",
            "confession_attitude": attitude,
        },
        "raw_description": _norm_text(raw)[:500],
    }


def _default_questions(defendant_name: str, charge: str, main_sentence: str) -> list[dict]:
    """当事人咨询问题库（系统LC阶段消费；数据txt没有，按罪名模板生成）。"""
    short_name = defendant_name or "我家人"
    return [
        {
            "question": f"{short_name}现在被关在看守所，律师能做什么？会见要什么手续？",
            "reference_answer": "辩护律师持律师执业证书、律师事务所证明和委托书即可会见在押的犯罪嫌疑人、被告人，了解案件有关情况、提供法律咨询等。侦查阶段律师还可以向侦查机关了解涉嫌的罪名和案件有关情况、申请取保候审、提出辩护意见。",
        },
        {
            "question": f"涉嫌{charge}，会判多久？能不能判缓刑？",
            "reference_answer": f"量刑需结合犯罪事实、性质、情节和对社会的危害程度综合判断。参考同类案件，本案判决结果为：{main_sentence[:120] or '需结合具体量刑情节判断'}。若具有自首、坦白、认罪认罚、赔偿谅解等从宽情节，可以依法从轻、减轻处罚；符合条件的可争取缓刑。",
        },
        {
            "question": "认罪认罚的话能轻判多少？签认罪认罚具结书有什么风险？",
            "reference_answer": "根据刑事诉讼法第十五条，自愿如实供述罪行、承认指控事实、愿意接受处罚的，可以依法从宽处理。签署具结书前应核实指控事实与罪名是否属实、量刑建议是否适当；对事实有异议的不要贸然签署，可先由律师阅卷后提出意见。",
        },
        {
            "question": "可以申请取保候审吗？要满足什么条件？",
            "reference_answer": "可能判处管制、拘役或独立适用附加刑的，或可能判处有期徒刑以上刑罚但采取取保候审不致发生社会危险性的，或患有严重疾病、生活不能自理等情形，可以申请取保候审。律师可代为提交取保候审申请书，侦查机关应在三日内答复。",
        },
    ]


# ── 案例切分与字段提取 ─────────────────────────────────────
def split_cases(lines: list[str]) -> list[dict]:
    """切分为 [{chapter, subsection, title, body_lines}]。"""
    cases: list[dict] = []
    chapter = ""
    subsection = ""
    current: dict | None = None

    for line in lines:
        line = line.rstrip()
        if not line.strip():
            if current is not None:
                current["body_lines"].append("")
            continue

        m = CHAPTER_RE.match(line.strip())
        if m:
            chapter = m.group(1).strip()
            subsection = ""
            current = None
            continue
        if SECTION_HEADER_RE.match(line.strip()):
            continue

        m = SUBSECTION_RE.match(line.strip())
        if m and not CASE_TITLE_RE.match(line.strip()):
            subsection = m.group(2).strip()
            current = None
            continue

        m = CASE_TITLE_RE.match(line.strip())
        if (
            m
            and REAL_CASE_HINT_RE.search(m.group(2))
            and len(m.group(2)) <= MAX_TITLE_LEN
        ):
            if current is not None:
                cases.append(current)
            current = {
                "chapter": chapter,
                "subsection": subsection,
                "title": m.group(2).strip(),
                "body_lines": [],
            }
            continue

        if current is not None:
            current["body_lines"].append(line)

    if current is not None:
        cases.append(current)
    return cases


def extract_fields(body_lines: list[str]) -> dict[str, str]:
    """把连续行归组到最近出现的字段标签下。"""
    fields: dict[str, list[str]] = {}
    label = None
    for line in body_lines:
        stripped = line.strip()
        if not stripped:
            if label:
                fields.setdefault(label, []).append("")
            continue

        bm = BRACKET_RE.match(stripped)
        if bm and bm.group(1) in BRACKET_LABELS:
            label = bm.group(1)
            fields.setdefault(label, [])
            if bm.group(2).strip():
                fields[label].append(bm.group(2).strip())
            continue

        lm = LABEL_RE.match(stripped)
        if lm and lm.group(1) in VERDICT_LABELS:
            label = lm.group(1)
            fields.setdefault(label, [])
            rest = stripped[lm.end():].strip()
            if rest:
                fields[label].append(rest)
            continue

        # 无冒号变体：标签词独立成行（如"本院查明"单独一行，内容在下一行）
        bare = stripped.rstrip("：:")
        if bare in VERDICT_LABELS or bare in {
            "公诉机关指控", "公诉机关、抗诉机关指控", "公诉机关认为",
            "被告人辩称", "辩护人辩护意见", "经审理查明", "法院认为",
            "一审法院查明", "一审法院认为", "裁判结果", "一审认定",
        }:
            label = bare
            fields.setdefault(label, [])
            continue

        if label:
            fields[label].append(stripped)

    return {k: "\n".join(v).strip() for k, v in fields.items() if "\n".join(v).strip()}


def convert_case(idx: int, case: dict) -> dict | None:
    facts = extract_fields(case["body_lines"])
    title = case["title"]
    charge = case["subsection"] or case["chapter"]

    # 被告人姓名: 标题前段（"严某聪以危险方法危害公共安全案" → 严某聪）或被告人：行
    defendant_raw = facts.get("被告人") or facts.get("上诉人") or ""
    name_m = re.match(r"^(?:指导性案例\d+号[：:])?([^，,；;—\-（(]{2,12}?)(?:等|某\d|)?(?:以.{0,20}罪|犯|涉嫌)?", title)
    def_name = ""
    dm = re.match(r"^被告人[暨及和]?[^：:]{0,10}?([^\s，。；,]{2,8}?)[。，,；;.（(（\s]", f"被告人{defendant_raw[:20]}")
    if dm:
        def_name = dm.group(1)
    elif name_m:
        def_name = name_m.group(1).strip()
    if not def_name or def_name in {"辩护人", "公诉机关", "检察院"}:
        def_name = "某被告人"

    # ── 审级信息 ──
    full_text = "\n".join(case["body_lines"])
    is_second = bool(re.search(r"上诉人|原审被告人|二审|刑终", full_text)) and "刑终" in full_text or "原审" in full_text
    court_m = COURT_RE.search(facts.get("审理法院") or full_text)
    court_name = court_m.group(0) if court_m else ""
    case_num_m = CASE_NUMBER_RE.search(full_text)
    case_number = case_num_m.group(0) if case_num_m else ""

    # ── 裁判结果 ──
    judgment_text = (
        facts.get("裁判结果") or facts.get("判决结果") or facts.get("审判")
        or facts.get("一审判决") or facts.get("裁判") or facts.get("复核结果") or ""
    )
    judgment_items = _parse_judgment_items(judgment_text)
    main_sentence = _extract_main_sentence(judgment_items)

    # ── 法院查明/认为 ──
    court_finding = (
        facts.get("本院查明") or facts.get("审理查明") or facts.get("一审法院查明")
        or facts.get("经审理查明") or facts.get("基本案情") or facts.get("案情") or ""
    )
    court_opinion = (
        facts.get("本院认为") or facts.get("一审法院认为") or facts.get("法院认为")
        or facts.get("裁判理由") or fields_opinion(facts) or ""
    )
    accusation_fallback = facts.get("公诉机关指控") or facts.get("公诉机关、抗诉机关指控") or ""
    procedure = facts.get("审理经过") or ""
    prosecution = _extract_prosecution_opinion(facts, accusation_fallback)
    compulsory = _extract_compulsory_measure(defendant_raw, procedure + court_finding)
    factors = _sentencing_factors(facts, court_opinion, defendant_raw)
    legal_basis = facts.get("法条依据") or facts.get("法律依据") or facts.get("依据法条") or facts.get("相关法条") or ""

    has_core = bool(court_finding or prosecution["accusation"] or judgment_items)
    if not has_core:
        return None

    def_profile = _defendant_profile(def_name, defendant_raw + court_finding, title)
    def_profile["questions"] = _default_questions(def_name, charge, main_sentence)

    extracted: dict[str, Any] = {
        "case_type": "criminal",
        "case_cause": charge,
        "case_background": _norm_text(prosecution["accusation"] or court_finding)[:2000],
        "charge": charge,
        "party_info": {
            "defendant": def_profile,
            "prosecutor": {
                "name": (facts.get("公诉机关") or facts.get("公诉机") or facts.get("公诉人")
                         or "某某人民检察院"),
                "type": "公诉机关",
            },
        },
        "defendant_raw": _norm_text(defendant_raw)[:800],
        "defense_hint": _extract_defense_hint(facts),
        "compulsory_measures": compulsory,
        "sentencing_factors": factors,
        "prosecution": prosecution,
        "procedure_history": _norm_text(procedure)[:1500],
        "first_instance": {
            "court": court_name,
            "case_number": case_number,
            "court_finding": court_finding,
            "court_opinion": court_opinion,
            "prosecution_claim": prosecution,
            "final_judgment": {"judgment_result": judgment_items},
            "main_sentence": main_sentence,
            "legal_basis": legal_basis,
        },
        "second_instance": _second_instance_block(facts, title),
        "guiding_points": _norm_text(facts.get("裁判要点") or facts.get("裁判要旨") or "")[:1500],
        "source_title": title,
        "source_chapter": case["chapter"],
    }
    return {"original_id": idx, "extracted_info": extracted}


def fields_opinion(facts: dict) -> str:
    for key in ("本院认为", "评析", "点评"):
        if facts.get(key):
            return facts[key]
    return ""


def _second_instance_block(facts: dict, title: str) -> dict:
    appeal = facts.get("上诉") or ""
    second_finding = ""
    second_opinion = ""
    second_judgment: list[str] = []
    if "驳回上诉" in facts.get("本院认为", "") + facts.get("裁判结果", ""):
        second_judgment = ["驳回上诉，维持原判。"]
    elif facts.get("裁判结果"):
        items = _parse_judgment_items(facts["裁判结果"])
        if any("改判" in x or "撤销" in x for x in items):
            second_judgment = items
    if appeal or second_judgment:
        second_finding = facts.get("本院查明") or "经二审审理查明的事实和证据与一审相同。"
        second_opinion = facts.get("本院认为") or facts.get("裁判理由") or ""
    return {
        "has_appeal": bool(appeal or second_judgment or "刑终" in title),
        "appeal_reason": _norm_text(appeal)[:1000],
        "court_finding": second_finding[:1500],
        "court_opinion": second_opinion[:2000],
        "final_judgment": {"judgment_result": second_judgment},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--max-cases", type=int, default=0, help="0 = all")
    args = parser.parse_args()

    if not args.source.exists():
        print(f"[ERROR] source not found: {args.source}", file=sys.stderr)
        return 1

    text = args.source.read_text(encoding="utf-8")
    lines = text.splitlines()
    raw_cases = split_cases(lines)
    print(f"[1/3] 切分出 {len(raw_cases)} 个案例")

    converted: list[dict] = []
    skipped: list[str] = []
    for idx, case in enumerate(raw_cases, start=1):
        result = convert_case(idx, case)
        if result is None:
            skipped.append(case["title"][:40])
            continue
        converted.append(result)
        if args.max_cases and len(converted) >= args.max_cases:
            break

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        json.dump(converted, fh, ensure_ascii=False, indent=2)

    charges: dict[str, int] = {}
    for c in converted:
        ch = c["extracted_info"]["case_cause"]
        charges[ch] = charges.get(ch, 0) + 1

    print(f"[2/3] 成功转换 {len(converted)} 个案例，跳过 {len(skipped)} 个（缺核心字段）")
    if skipped:
        print("  跳过列表（前10）:")
        for s in skipped[:10]:
            print(f"    - {s}")
    print(f"[3/3] 写入 {args.out}")
    print(f"  罪名分布（top 15）:")
    for ch, n in sorted(charges.items(), key=lambda x: -x[1])[:15]:
        print(f"    {ch}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
