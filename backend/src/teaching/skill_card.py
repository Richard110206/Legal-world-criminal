"""技能卡闭环：LearningEvent → 学生个人 SKILL.md 技能卡 → 下局注入。

流程（借鉴上游 gitskill 的 reflection→skill→inject 模式，简化为教学场景）：
  1. 阶段评分产出 LearningEvent（error_tags / knowledge_gaps / overall_feedback）
  2. `update_skill_cards` 把弱点+教训沉淀为一张张技能卡（每卡一个知识点，
     Markdown + YAML frontmatter，与 legal-skillhub 的 SKILL.md 格式兼容）
  3. 卡片落盘 `sandbox_data/teaching/skill_cards/{student_id}/{kp-slug}/SKILL.md`
  4. 律师 agent 的技能目录扫描会把它们与公共技能库一起暴露给 load_skill——
     学生下一局开场即可"加载自己上局沉淀的技能卡"补弱

卡片由规则生成（不调 LLM）：内容直接来自裁判给出的 evidence/rationale，
保证可溯源、零成本、离线可用。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SKILL_CARD_SCHEMA = "skill-card-v1"
DEFAULT_SKILL_CARDS_ROOT = (
    Path(__file__).resolve().parents[2] / "sandbox_data" / "teaching" / "skill_cards"
)

# 每个学生最多保留的卡片数（按更新时间淘汰最旧的）
MAX_CARDS_PER_STUDENT = 24


def _skill_cards_root() -> Path:
    import os

    return Path(
        os.environ.get("SIMLAW_TEACHING_SKILL_CARDS_DIR") or DEFAULT_SKILL_CARDS_ROOT
    ).resolve()


def _slugify(text: str) -> str:
    """中文知识点 → 安全目录名（保留中文，去标点，截断长度）。"""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1F\s]+', "-", str(text or "").strip())
    cleaned = cleaned.strip("-.").strip()
    return cleaned[:40] or "unnamed"


def student_skill_dir(student_id: str) -> Path:
    safe_id = "".join(
        ch for ch in str(student_id or "anonymous").strip() if ch.isalnum() or ch in "_-"
    ) or "anonymous"
    return _skill_cards_root() / safe_id


def _card_name(kp: str) -> str:
    return f"student-skill-{_slugify(kp)}"


def _weak_capabilities(event: dict[str, Any], ceiling: float = 0.6) -> list[dict[str, Any]]:
    """低于阈值的能力项（带 rationale/evidence），按分数升序。"""
    items = []
    for code, entry in (event.get("capability_scores") or {}).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("score") is None:
            continue  # 弃权维（judge 未评）不得当作 0 分出卡
        try:
            score = float(entry.get("score") or 0.0)
        except (TypeError, ValueError):
            continue
        if score <= ceiling:
            items.append(
                {
                    "code": code,
                    "score": score,
                    "rationale": str(entry.get("rationale") or "").strip(),
                    "evidence": str(entry.get("evidence_quote") or "").strip(),
                }
            )
    items.sort(key=lambda x: x["score"])
    return items


def _render_card(
    *,
    kp: str,
    stage: str,
    case_id: str,
    charge: str,
    status: str,
    reason: str,
    weak_caps: list[dict[str, Any]],
    error_tags: list[str],
    feedback: str,
    previous: dict[str, Any] | None,
) -> str:
    """渲染一张技能卡 Markdown（frontmatter + 正文）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    streak = int((previous or {}).get("review_count") or 0) + 1
    history = list((previous or {}).get("status_history") or [])
    if history and history[-1] == status:
        pass
    history.append(status)
    history = history[-8:]

    cap_lines = []
    for cap in weak_caps[:3]:
        line = f"- **{cap['code']}**（{cap['score']:.1f}/1.0）：{cap['rationale'] or '见导师总评'}"
        if cap["evidence"]:
            line += f"\n  - 当时你的发言：「{cap['evidence'][:120]}」"
        cap_lines.append(line)

    tag_lines = [f"- {tag}" for tag in error_tags[:6]] or ["-（本次无具体错误标记）"]

    history_line = " → ".join(history) if history else status

    body = f"""---
name: {_card_name(kp)}
description: 你在{stage}阶段暴露的知识点「{kp}」薄弱卡——办案前先读，避免重复失分。
student_skill_card: true
knowledge_point: {kp}
stage: {stage}
charge: {charge}
review_count: {streak}
status_history: [{", ".join(f'"{h}"' for h in history)}]
updated_at: {today}
---

# 技能卡：{kp}

> 来源：{case_id} · {stage}阶段批阅 · {today}
> 掌握轨迹：{history_line}

## 为什么有这张卡

{reason or f"你在{stage}阶段的发言中，知识点「{kp}」被判定为「{status}」。"}

## 下次办案先检查

{chr(10).join(cap_lines) or "-（本项未关联到具体能力短板，通读总评即可）"}

## 已犯过的错（别再犯）

{chr(10).join(tag_lines)}

## 导师当时的话

{feedback or "（无）"}

## 使用方式

接手新案件、进入{stage}相关阶段前，先过一遍这张卡；案件结束对照检查是否重蹈覆辙。
"""
    return body


def update_skill_cards(student_id: str, event: dict[str, Any]) -> list[Path]:
    """从 LearningEvent 生成/更新技能卡；返回写盘的卡片路径列表。"""
    student_id = str(student_id or "anonymous").strip() or "anonymous"
    stage = str(event.get("stage") or "").strip().upper()
    case_id = str(event.get("case_id") or "")
    charge = str(event.get("charge") or "")
    feedback = str(event.get("overall_feedback") or "").strip()
    error_tags = [str(t) for t in (event.get("error_tags") or [])]

    verdicts = {str(v.get("kp") or "").strip(): v for v in (event.get("knowledge_verdicts") or []) if isinstance(v, dict)}
    gaps = [str(g).strip() for g in (event.get("knowledge_gaps") or []) if str(g).strip()]

    # 卡片目标：所有缺口 + 判定为 partial/missing 的知识点
    targets: dict[str, dict[str, Any]] = {}
    for kp in gaps:
        targets.setdefault(kp, {"kp": kp, "status": "missing", "reason": ""})
    for kp, verdict in verdicts.items():
        if not kp:
            continue
        status = str(verdict.get("status") or "").strip()
        if status in ("missing", "partial"):
            targets.setdefault(kp, {"kp": kp, "status": status, "reason": str(verdict.get("reason") or "")})
        elif kp in targets and status == "mastered":
            # 本阶段已攻克 → 从待补清单移除（但保留历史卡，更新状态轨迹）
            targets[kp]["status"] = "mastered"
            targets[kp]["reason"] = str(verdict.get("reason") or "")

    if not targets:
        return []

    weak_caps = _weak_capabilities(event)
    out_dir = student_skill_dir(student_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for kp, info in targets.items():
        card_dir = out_dir / _slugify(kp)
        card_dir.mkdir(parents=True, exist_ok=True)
        card_path = card_dir / "SKILL.md"

        previous = None
        if card_path.exists():
            previous = _read_frontmatter(card_path)

        content = _render_card(
            kp=kp,
            stage=stage,
            case_id=case_id,
            charge=charge,
            status=info["status"],
            reason=info["reason"],
            weak_caps=weak_caps,
            error_tags=error_tags,
            feedback=feedback,
            previous=previous,
        )
        card_path.write_text(content, encoding="utf-8")
        written.append(card_path)
        logger.info("[SkillCard] wrote %s (%s/%s)", card_path.name, student_id, kp)

    _evict_stale(out_dir)
    return written


def _read_frontmatter(path: Path) -> dict[str, Any] | None:
    """轻量解析卡片 frontmatter（review_count/status_history 及展示字段）。"""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fm: dict[str, Any] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key == "review_count":
            try:
                fm["review_count"] = int(value)
            except ValueError:
                pass
        elif key == "status_history":
            fm["status_history"] = [
                item.strip().strip('"')
                for item in value.strip("[]").split(",")
                if item.strip()
            ]
        elif key in {"name", "description", "knowledge_point", "stage", "charge", "updated_at"}:
            fm[key] = value
    return fm or None


def _evict_stale(root: Path) -> None:
    """超过上限时按 updated_at 淘汰最旧的卡（mastered 的优先淘汰）。"""
    cards = sorted(
        (p for p in root.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for card in cards[MAX_CARDS_PER_STUDENT:]:
        import shutil

        shutil.rmtree(card, ignore_errors=True)
        logger.info("[SkillCard] evicted stale card %s", card.name)


def list_skill_cards(student_id: str) -> list[dict[str, Any]]:
    """列出学生全部技能卡（供 API/前端展示）。"""
    root = student_skill_dir(student_id)
    cards: list[dict[str, Any]] = []
    if not root.is_dir():
        return cards
    for card_dir in sorted(root.iterdir()):
        path = card_dir / "SKILL.md"
        if not path.exists():
            continue
        fm = _read_frontmatter(path) or {}
        cards.append(
            {
                "name": fm.get("name") or card_dir.name,
                "description": fm.get("description", ""),
                "knowledge_point": fm.get("knowledge_point") or card_dir.name,
                "stage": fm.get("stage", ""),
                "charge": fm.get("charge", ""),
                "slug": card_dir.name,
                "review_count": fm.get("review_count", 1),
                "status_history": fm.get("status_history", []),
                "updated_at": fm.get("updated_at") or datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d"),
            }
        )
    return cards


def read_skill_card(student_id: str, slug: str) -> dict[str, Any] | None:
    """读取单张技能卡全文（slug 为卡片目录名）。"""
    root = student_skill_dir(student_id)
    # slug 来自 URL 路径，拒绝任何穿越尝试
    safe_slug = _slugify(slug)
    card_dir = root / safe_slug
    path = card_dir / "SKILL.md"
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    fm = _read_frontmatter(path) or {}
    return {
        "name": fm.get("name") or safe_slug,
        "description": fm.get("description", ""),
        "knowledge_point": fm.get("knowledge_point") or safe_slug,
        "stage": fm.get("stage", ""),
        "slug": safe_slug,
        "review_count": fm.get("review_count", 1),
        "content": content,
    }


__all__ = [
    "update_skill_cards",
    "list_skill_cards",
    "read_skill_card",
    "student_skill_dir",
    "SKILL_CARD_SCHEMA",
]
