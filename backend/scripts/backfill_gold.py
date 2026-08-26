# -*- coding: utf-8 -*-
"""金标准回填：为缺 guiding_points / defense_hint 的案子生成两个字段。

用法（cwd=backend）：
  .venv\\Scripts\\python.exe -X utf8 scripts\\backfill_gold.py --limit 3      # 干跑 3 案
  .venv\\Scripts\\python.exe -X utf8 scripts\\backfill_gold.py --apply       # 全量回填合并

流程：
  1. 扫描 dataset/criminal_case_dataset.json，找 guiding_points/defense_hint
     为空（None/空串）的案子
  2. 用 DeepSeek 以已有字段（charge/case_background/defense_stage/trial_stage/
     sentencing_factors 等）为原料生成缺失字段——只概括、不新造事实
  3. 生成结果先写 dataset/backfill_staging.json（带 case 标识与原文长度，
     供人工抽检）；--apply 时才合并进主数据集（先备份）

生成质量约束（写进 prompt）：
  - guiding_points：2-4 条教学要点，面向"学生辩护时应掌握的裁判规则"
  - defense_hint：给学生的辩护思路提示（不是标准答案），方向性建议
  - 不得虚构案情、罪名、数字；只能来自输入字段
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DATASET_PATH = BACKEND_ROOT.parent / "dataset" / "criminal_case_dataset.json"
STAGING_PATH = BACKEND_ROOT.parent / "dataset" / "backfill_staging.json"

TARGET_FIELDS = ("guiding_points", "defense_hint")


def _is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def build_backfill_prompt(info: dict, missing: list[str]) -> str:
    source = {
        "charge": info.get("charge", ""),
        "case_cause": info.get("case_cause", ""),
        "case_background": info.get("case_background", ""),
        "sentencing_factors": info.get("sentencing_factors", ""),
        "defense_stage": info.get("defense_stage", ""),
        "trial_stage": info.get("trial_stage", ""),
        "first_instance": info.get("first_instance", ""),
    }
    source = {k: v for k, v in source.items() if v}
    field_specs = {
        "guiding_points": (
            '"guiding_points"：2-4 条该案的教学要点（裁判规则/构成要件辨析/证据认定标准），'
            "每条一句话，面向刑法学生辩护训练，格式：纯文本，条目间用分号或换行。"
        ),
        "defense_hint": (
            '"defense_hint"：给学生的辩护思路提示（不是标准答案），'
            "指出本案值得考虑的辩护方向（无罪/罪轻/量刑/程序）与理由要点，120-200 字纯文本。"
        ),
    }
    wanted = "\n".join(f"- {field_specs[m]}" for m in missing)
    return (
        "你是刑事辩护教学的案例标注专家。以下是一个真实刑事案例的结构化信息，"
        f"需要补充标注 {len(missing)} 个教学金标准字段。\n\n"
        "[案例信息]\n"
        f"{json.dumps(source, ensure_ascii=False, indent=1)[:6000]}\n\n"
        f"[需要生成的字段]\n{wanted}\n\n"
        "[硬性约束]\n"
        "1. 只能基于上面给出的案例信息概括，严禁虚构案情、罪名、数额、人名。\n"
        "2. guiding_points 是给学生看的裁判规则要点，不是复述案情。\n"
        "3. defense_hint 是方向性提示，不给出完整辩护词。\n"
        "4. 只返回一个 JSON 对象，不要 markdown 代码块："
        + json.dumps({m: "生成的文本" for m in missing}, ensure_ascii=False)
    )


def _extract_json(text: str) -> dict | None:
    import re

    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def create_llm():
    from camel.agents import ChatAgent
    from camel.models import ModelFactory
    from camel.types import ModelPlatformType

    from src.utils.model_config import build_runtime_openai_chat_config, resolve_openai_chat_model

    model_type = resolve_openai_chat_model()
    model = ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=model_type,
        model_config_dict=build_runtime_openai_chat_config(
            model_name=model_type,
            temperature=0.2,
            max_tokens=1500,
        ),
    )
    return ChatAgent(
        system_message="你是严谨的刑事案例标注助手，只输出合法 JSON。", model=model
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 guiding_points/defense_hint 金标准")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个案子（0=全部）")
    parser.add_argument("--apply", action="store_true", help="把 staging 合并进主数据集")
    parser.add_argument("--redo", action="store_true", help="忽略已有 staging，重新生成")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(BACKEND_ROOT / ".env")

    if not DATASET_PATH.exists():
        print(f"[FAIL] dataset not found: {DATASET_PATH}")
        return 1

    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    cases = data if isinstance(data, list) else list(data.values())
    is_list = isinstance(data, list)

    if args.apply:
        if not STAGING_PATH.exists():
            print("[FAIL] staging 文件不存在，先干跑生成")
            return 1
        staging = json.loads(STAGING_PATH.read_text(encoding="utf-8"))
        backup = DATASET_PATH.with_suffix(".json.bak")
        shutil.copy2(DATASET_PATH, backup)
        patched = 0
        for item in staging["items"]:
            idx = item["index"]
            case = data[idx] if is_list else data[item["original_id"]]
            info = case.setdefault("extracted_info", {})
            for field, value in item["generated"].items():
                if value:
                    info[field] = value
                    patched += 1
        DATASET_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        # re-verify coverage
        data2 = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
        cases2 = data2 if isinstance(data2, list) else list(data2.values())
        gp = sum(1 for c in cases2 if not _is_empty((c.get("extracted_info") or {}).get("guiding_points")))
        dh = sum(1 for c in cases2 if not _is_empty((c.get("extracted_info") or {}).get("defense_hint")))
        print(f"[APPLY] 已合并 {patched} 个字段；备份在 {backup.name}")
        print(f"[APPLY] 覆盖率：guiding_points {gp}/124，defense_hint {dh}/124")
        return 0

    # ── dry-run / generation ──
    targets: list[tuple[int, str, dict, list[str]]] = []
    for idx, case in enumerate(cases):
        info = case.get("extracted_info") or {}
        missing = [f for f in TARGET_FIELDS if _is_empty(info.get(f))]
        if missing:
            targets.append((idx, str(case.get("original_id", "")), info, missing))

    if not targets:
        print("[OK] 124 案金标准已齐全，无需回填")
        return 0

    if args.limit:
        targets = targets[: args.limit]

    # resume from existing staging unless --redo
    done: dict[int, dict] = {}
    if STAGING_PATH.exists() and not args.redo:
        prev = json.loads(STAGING_PATH.read_text(encoding="utf-8"))
        done = {item["index"]: item for item in prev.get("items", [])}
        print(f"[RESUME] 已有 staging 记录 {len(done)} 条")

    print(f"[PLAN] 待生成：{len(targets)} 案（guiding_points 缺 {sum(1 for t in targets if 'guiding_points' in t[3])}，"
          f"defense_hint 缺 {sum(1 for t in targets if 'defense_hint' in t[3])}）")

    agent = create_llm()
    from camel.messages import BaseMessage

    items = list(done.values())
    failed = 0
    for i, (idx, oid, info, missing) in enumerate(targets, 1):
        if idx in done:
            continue
        prompt = build_backfill_prompt(info, missing)
        try:
            agent.reset()
            resp = agent.step(
                BaseMessage.make_user_message(role_name="user", content=prompt)
            )
            payload = _extract_json(resp.msgs[0].content)
            generated = {}
            for field in missing:
                value = str((payload or {}).get(field, "")).strip()
                # 长度健康检查：太短说明模型敷衍
                if field == "guiding_points" and len(value) < 40:
                    value = ""
                if field == "defense_hint" and len(value) < 60:
                    value = ""
                generated[field] = value
            ok = any(generated.values())
            if not ok:
                failed += 1
            items.append(
                {
                    "index": idx,
                    "original_id": oid,
                    "charge": str(info.get("charge", "")),
                    "missing": missing,
                    "generated": generated,
                    "ok": ok,
                }
            )
            status = "OK" if ok else "EMPTY"
            print(f"  [{i}/{len(targets)}] case {oid}（{info.get('charge', '?')}）→ {status}")
        except Exception as exc:
            failed += 1
            items.append(
                {
                    "index": idx,
                    "original_id": oid,
                    "charge": str(info.get("charge", "")),
                    "missing": missing,
                    "generated": {},
                    "ok": False,
                    "error": str(exc)[:200],
                }
            )
            print(f"  [{i}/{len(targets)}] case {oid} → ERROR: {exc}")
        # checkpoint staging every 5 cases
        if i % 5 == 0 or i == len(targets):
            STAGING_PATH.write_text(
                json.dumps(
                    {"generated_at": datetime.now().isoformat(timespec="seconds"), "items": items},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        time.sleep(0.3)  # DeepSeek rate courtesy

    STAGING_PATH.write_text(
        json.dumps(
            {"generated_at": datetime.now().isoformat(timespec="seconds"), "items": items},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\n[DONE] 生成 {len(items)} 条记录（失败 {failed}）→ {STAGING_PATH}")
    print("人工抽检后运行 --apply 合并进主数据集。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
