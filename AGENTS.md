# AGENTS.md — 项目架构与决策记忆

> 面向开发者的项目全景文档：定位、架构、教学评分系统、关键技术决策与待办。

---

## 一、项目定位

**LegalWorld 刑法版（纯刑事）**——"星火智学"比赛项目（XH-202620）的**精学模块**：刑事公诉案件全流程 AI 仿真教学环境。学生在真实刑事案件中扮演辩护律师，AI 扮演检察官/法官/当事人对抗，系统找出回答漏洞、做法条溯源、按能力框架评分、沉淀补弱技能卡。

- 上游：https://github.com/chidaic/Legal-world.git（民事法律小镇，本项目已移除全部民事流程）
- 产品形态：星火智学四模块闭环（预习 → **精学** → 复习 → 教师闭环）中的**精学模块（智能体编排）**
- **范围边界**：预习（知识地图/前测）、复习（变式/角色互换）、教师闭环（班级学情）为并列独立模块，不在本仓库范围内。模块间仅约定数据接口——精学模块输出 LearningEvent / LearnerProfile / 技能卡（`learning-event-v1` 等 schema）供其他模块消费。

---

## 二、技术栈与运行方式

| 项 | 值 |
|----|-----|
| 后端 | Python + FastAPI + uvicorn（WebSocket 驱动） |
| 前端 | Vue 3 + Vite + TypeScript（除 vue 外零运行时依赖） |
| LLM | DeepSeek（camel-ai 框架驱动，兼容 OpenAI 协议端点） |
| NLI | 本地中文 cross-encoder（IDEA-CCNL/Erlangshen-Roberta-330M-NLI，CPU 可跑） |
| 数据 | 124 件真实刑事案例（含全量金标准）+ 刑法 504 条/刑诉法 308 条本地法条库 |
| 玩家模式 | `SIMLAW_PLAYER_LAWYER_MODE=defendant` |

**启动**：
```bash
# 后端（cwd=backend，端口 8000）
../.venv/Scripts/python.exe -m uvicorn ws_server:app --host 127.0.0.1 --port 8000

# 前端（cwd=frontend，端口 5173）
npm run dev

# 整体验证（模块导入 + manifest + FSM + teaching）
cd backend && ../.venv/Scripts/python.exe scripts/verify_criminal.py
```

> 必须使用 `.venv` 中的 Python（系统 Python 无 camel 依赖）。LLM 配置见 `.env`（不入库）。

---

## 三、刑事流程状态机（核心架构）

```
接受委托 → 侦查阶段 → 审查起诉 → 辩护词起草 → 刑事一审 → 上诉决策 → 刑事二审 → 终审
    LC        INV         PR          DS          CR         CRA
```

**主链路**：
```
空闲 → 前台接待 → 委托洽谈(LC) → 侦查(INV) → 审查起诉(PR) → 起诉书递交
  → 辩护词起草(DS) → 一审庭审(CR) → 一审判决
  → [服判 → 已结案 | 上诉 → 二审(CRA) → 终审判决 → 已结案]
```

**提前终止分支**（辩护效果真实反馈）：
- PR 阶段检察官不起诉判定：辩护意见成立（证据不足/不构成犯罪/情节显著轻微）→ 提前结案（辩护成功）
- 一审判决后服判（数据集无二审数据的案件）→ 判决生效结案

**关键文件**：
- `backend/src/core/event_bus.py` — 事件总线（EventType 枚举 + 发布订阅）
- `backend/src/orchestration/case_fsm.py` — CaseState + VALID_TRANSITIONS 迁移图 + SHARED_CASE_STATES
- `backend/src/orchestration/scenario_orchestrator.py` — 编排器（事件订阅 → 场景调度 → 阶段结束触发教学评分）
- `backend/src/scenarios/` — 六个阶段场景（legal_consultation / investigation / prosecution_review / defense_opinion_drafting / criminal_trial / criminal_appeal_trial）

**角色**：委托人（家属）、被告人、辩护律师（学生扮演）、检察官、法官、侦查人员（可选）。
**刑事特有程序**：取保候审、非法证据排除、认罪认罚从宽、最后陈述、上诉/抗诉。

**阶段工具权限**：`src/pipeline/stage_tool_manifest.yaml` 声明每阶段每角色工具（agent_type_defaults 常驻 + role_tools 阶段专属），`stage_tool_resolver.py` 在进入/退出阶段时 apply/clear。

---

## 四、教学评分系统（teaching/）

### 评分管线（四层）

```
学生发言（玩家 ledger submissions）
  ├─ ① 规则层：法条引用存在性核验（citation_check，本地法条库精确匹配）
  ├─ ② NLI 层：引用三段论对齐（citation_alignment，CitaLaw 式评估，arXiv 2412.14556）
  │     premise=法条原文（本地语料解析，非学生转述），hypothesis=学生论断
  │     双层裁决：本地 cross-encoder（确定性）+ LLM 裁判（一次批量调用）
  │     融合：一致采信 / 冲突采信裁判并标 layer_conflict / 双败 neutral
  ├─ ③ LLM-as-judge 层：DeepSeek 按 8 能力 rubric 打分（含金标准对照）
  └─ ④ 确定性覆盖层：rule_retrieval 由公式分直接计算（deterministic.py）
        score = 0.4×基础分(有效引用率) + 0.6×语义分(NLI 对齐率)
        每分带 formula 审计字段；judge 分保留为 judge_raw_score 交叉核验
```

### 8 能力框架（CJ-Bench 刑法化，唯一权威 `teaching/rubrics.py`）

| 能力码 | 中文名 | 主考阶段 |
|--------|--------|---------|
| fact_identification | 事实识别 | LC/INV |
| rule_retrieval | 规范检索（确定性评分） | PR/DS |
| subsumption | **要件涵摄★**（三栏表专项） | PR/DS/CR/CRA |
| claim_construction | 辩护主张构建 | PR/DS/CRA |
| evidence_marshalling | 证据组织 | DS/CR |
| evidentiary_advocacy | 质证对抗 | CR/CRA |
| position_consistency | 立场一致性 | DS/CR/CRA |
| procedural_compliance | 程序合规 | LC/INV/PR/CR |

阶段 × 能力矩阵见 `STAGE_CAPABILITY_MATRIX`（primary 权重 1.0 / secondary 0.5）。

### LearningEvent 输出（`learning-event-v1`）

```json
{
  "event_id": "evt_YYYYmmdd_HHMMSS_{case_id}_{stage}",
  "student_id": "...", "case_id": "...", "stage": "DS",
  "capability_scores": {
    "rule_retrieval": {"score": 0.7, "raw": 7, "weight": 1.0, "source": "deterministic|judge|missing",
                        "formula": "...", "judge_raw_score": 8, "rationale": "...", "evidence_quote": "...",
                        "unverified": true}
  },
  "subsumption_table": [{"element": "...", "fact_found": "...", "conclusion": "符合|不符合|存疑", "comment": "..."}],
  "knowledge_verdicts": [{"kp": "...", "status": "mastered|partial|missing", "reason": "..."}],
  "error_tags": ["法条引用错误-264与266混淆"],
  "law_citations": [{"citation": "《刑法》第二百六十四条", "status": "valid|invalid_article|invalid_title"}],
  "citation_alignment": [{"sentence": "...", "verdict": "supports|contradicts|neutral", "layers": [...]}],
  "knowledge_gaps": ["..."], "overall_feedback": "...", "scored_at": "..."
}
```

注：`score` 可能为 null（judge 弃权维，`source: "missing"`），消费方渲染时需跳过；`unverified: true` 表示裁判声称的 evidence_quote 未在学生发言中找到（回验机制）。

### 模块文件

```
teaching/
├── rubrics.py              # 8 能力 + 阶段矩阵 + judge 提示词 + 涵摄三栏表专项
├── deterministic.py        # rule_retrieval 确定性公式分（覆盖 judge 主观分）
├── citation_alignment.py   # NLI 引用三段论对齐（本地模型 + LLM 裁判双层）
├── citation_check.py       # 即时法条校验（错误条号 + BM25 相近法条建议）
├── law_corpus.py           # 本地法条检索/核验（BM25 + BM25F 字段加权，纯 stdlib 离线可用）
├── transcript.py           # 学生发言 + 金标准组装
├── scorer.py               # 评分编排：四层管线 → LearningEvent
├── learner.py              # 跨案件画像（加权累计，弃权维不计入）
├── report.py               # 雷达/成长曲线/知识缺口/quiz 推荐
├── skill_card.py           # 技能卡生成与读取（学生补弱卡 SKILL.md）
└── routes.py               # /api/teaching/* 路由
```

### 触发链路

1. **即时校验**：玩家每次提交发言，`submit_response` 同步返回 `citation_feedback`（琥珀警示/绿色通过条）
2. **阶段自动评分**：阶段结束异步触发（daemon 线程，不阻塞流程），仅玩家模式
3. **画像与技能卡**：评分落盘后自动累计 `sandbox_data/teaching/profiles/` 与 `skill_cards/{student_id}/`
4. **技能卡闭环**：下局开局面板可查看/勾选历史技能卡（最多 3 张），提交发言时附提醒块——AI 陪练可见并据此回应

### API

| 方法/路径 | 用途 |
|----------|------|
| POST `/api/teaching/score` | 手动触发评分 |
| GET `/api/teaching/event/{case_id}/{stage}` | 单次 LearningEvent |
| GET `/api/teaching/profile/{student_id}` | 学习者画像 |
| GET `/api/teaching/report/{student_id}` | 学期报告 + 推荐 + 技能卡列表 |
| GET `/api/teaching/skill-cards/{student_id}[/{slug}]` | 技能卡列表/全文 |
| GET `/api/teaching/corpus` | 法条库状态 |

### 数据来源

- 学生发言：`case_output_dir/_player_lawyer/player_run_ledger.json`
- 对话上下文：`case_output_dir/{stage}_result.json` 的 dialog_history
- 金标准：`dataset/criminal_case_dataset.json`（124 案全覆盖：五阶段字段 + guiding_points + defense_hint）

---

## 五、关键技术决策

### 工程化基建（2026-08-30 重构，全部验证通过后落地）

1. **API 层模块化**：原 4536 行 `ws_server.py` 巨石拆为 `src/api/` 17 个模块（app_state 进程单例 / deps 依赖 / 6 组路由 / ws_endpoint / lifecycle / 领域服务），`ws_server.py` 保留 17 行薄入口，`uvicorn ws_server:app` 契约不变。循环依赖三招：bottom-import、lazy trampoline、使用点延迟 import——ruff I001 会重排 import 破坏加载次序，改 api 包后必须 `import ws_server` 冒烟
2. **评分任务队列**：daemon 线程（进程退出即丢）→ `teaching/task_queue.py`（SQLite WAL 持久化 + 2 worker 线程池 + 幂等键 + 崩溃恢复 + 3 次重试），`GET /api/teaching/scoring-tasks` 监控、`POST .../retry-failed` 运维
3. **pytest 正规化**：`backend/tests/`（conftest 全隔离：NLI 禁载、目录指向 tmp、FakeJudge），26 用例 ~5s；`scripts/test_teaching.py` 转 wrapper
4. **lint/CI**：根 `pyproject.toml`（ruff 规则集 + pytest 配置 + per-file-ignores），`.github/workflows/ci.yml`：ruff → 装配冒烟（≥50 路由）→ pytest → verify_criminal → vue-tsc + build。存量债务（F811 双定义等）带注释豁免，待专项清理
5. **集中配置**：`src/config.py` pydantic-settings 分组，`get_settings().cache_clear()` 于测试；`law_embedding` 硬编码端点已移除
6. **依赖治理**：删除 PyPI 废弃包 `pathlib`（会污染环境），requirements 修 UTF-8；前端加 ESLint 9 flat config

### 评分可靠性（已落地）

1. **rule_retrieval 确定化**：条号核验 + NLI 对齐是可计算证据，公式分取代 LLM 自由裁量；judge 分保留做交叉核验，偏差 ≥2 分自动标注
2. **弃权语义**：judge 漏评某能力时 `score=null`（非 0 分），画像累计跳过——"未评"不拉低学生
3. **evidence_quote 回验**：裁判引语回查学生发言全文，查不到标 `unverified`（分数不动，可质疑性保留）
4. **judge agent 隔离**：camel ChatAgent 会累积对话 memory，每次评阅前 reset，防止跨案件上下文污染
5. **口径统一**：雷达图与成长曲线同为加权平均（sum(score×weight)/sum(weight)）

### 法条检索

- 本地法条库 `backend/legal_corpus/processed/*.jsonl`（《刑法》《刑诉法》PDF 经 PyMuPDF 构建），**BM25(k1=1.5, b=0.75) + BM25F 字段加权**（content 1.0 / article_ref 4.0 / 条号精确命中 ×3），纯 stdlib，离线可用
- 不接 Qdrant/远程向量库：词法检索已满足条号+语义兜底需求，语义检索留作增强
- Dify 法条 API（`DifyCitationSource`）为可插拔设计，接口修通后启用
- 元典 MCP 工具已注册常驻，依赖外网，失败降级不阻断

### NLI 模型层

- 首选 IDEA-CCNL/Erlangshen-Roberta-330M-NLI（机构背书的中文 NLI）；英文模型不得兜底（中文输入下是噪声源），加载失败直接走 LLM-only
- env 开关：`SIMLAW_NLI_MODEL_DISABLED`（关模型层）、`SIMLAW_NLI_MODEL_NAME`（换模型）
- 模型偏保守（法条刑罚段对"构成该罪"论断常判 neutral）属正常，融合层由 LLM 裁判兜底

### 微调定位

现阶段不需要。事实知识靠 RAG 与规则层，评分先用 prompt 工程 + 确定性公式；积累 50+ 评分数据后再考虑 LoRA 对比实验。

---

## 六、精学智能体角色

| 角色 | 职责 | 禁止 | 落地 |
|------|------|------|------|
| 案件导演 | 初始化案件、控制阶段、按权限揭示事实 | 生成未审核的标准答案 | ✅ orchestrator + 场景层 |
| 检索研究员 | 调用可信知识服务获得法条/案例 | 绕过证据源自由引用 | ✅ 本地法条库 + BM25 + 元典 MCP |
| AI 对抗辩手 | 站在相反立场质询反驳学生 | 虚构事实或法源 | △ 庭审对抗已有，主动追问待增强 |
| 证据审查员 | 检查事实、证据、引用一致性 | 代替法学审核定争议结论 | ✅ 引用核验（规则 + NLI 双层） |
| 教学裁判/导师 | 按 rubric 给形成性反馈 | 用于正式成绩全自动评定 | ✅ 四层评分管线 |

---

## 七、待办

### 未排期
- [ ] 对抗质询增强：检察官主动指出辩护漏洞并追问（方案：检察官场景配置加对抗指令 + 庭审主持 prompt 倾向性路由 + 追问上限防刷屏）
- [ ] CR 判决书全文输出（现为摘要句）
- [ ] Dify API 对接（等接口修通，可插拔设计已挂）
- [ ] 数字人/语音（讯飞能力）、论证图/证据板可视化、评分模型 LoRA 对比实验

---

## 八、常用验证命令

```bash
# 后端 lint（ruff，配置见根目录 pyproject.toml）
cd backend && ../.venv/Scripts/python.exe -m ruff check src ws_server.py

# 后端测试（pytest：teaching 离线套件 + 任务队列 + file_io，26 个用例）
cd backend && ../.venv/Scripts/python.exe -m pytest -q

# 后端整体验证（模块导入 + manifest + FSM + teaching）
cd backend && ../.venv/Scripts/python.exe -X utf8 scripts/verify_criminal.py

# 教学模块旧入口（转发到 pytest，向后兼容）
cd backend && ../.venv/Scripts/python.exe -X utf8 scripts/test_teaching.py

# 金标准回填（缺字段时）
cd backend && ../.venv/Scripts/python.exe -X utf8 scripts/backfill_gold.py [--limit N | --apply]

# 前端 lint + 类型检查 + 构建
cd frontend && npm run lint
cd frontend && npm run typecheck
cd frontend && npm run build
```

CI（`.github/workflows/ci.yml`）在 push/PR 时自动执行：ruff → 应用装配冒烟（55 路由）→ pytest → verify_criminal → 前端 vue-tsc + vite build。

---

*本文档记录项目设计思路与决策，作为开发协作的项目记忆。*
