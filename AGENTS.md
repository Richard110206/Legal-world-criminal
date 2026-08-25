# AGENTS.md — 项目持久化记忆

> 本文档是 opencode 的项目记忆。每次会话自动加载，用于快速了解：
> 项目定位、架构设计思路、已完成改造、教学化方向、评分系统、关键技术决策与未来改进方向。

---

## 一、项目定位

**LegalWorld 刑法版（纯刑事）**——为"星火智学"比赛项目服务的**刑事公诉案件全流程 AI 仿真环境**。

- 上游：https://github.com/chidaic/Legal-world.git（民事法律小镇）
- 本项目已完成：**移除全部民事流程**，仅保留刑事公诉流程 + 通用基础设施
- 比赛全称：面向一流学科建设的学科垂类大模型与创新应用开发比赛（XH-202620）
- 产品文档：`E:\大二下活动\【揭榜挂帅】法律一流学科建设\7.27第二次资料收集整理\_md\01_产品架构.md`（星火智学完整产品形态说明书）

**最终产品方向**：面向刑法本科教学的"案例教学法 + LLM 辅助教学"系统——学生在真实刑事案件中扮演辩护律师，系统找出其回答漏洞、做法条溯源、打分、标记知识点欠缺。

---

## 二、技术栈与运行方式

| 项 | 值 |
|----|-----|
| 后端 | Python + FastAPI + uvicorn（WebSocket 驱动） |
| 前端 | Vue 3 + Vite + TypeScript |
| 入口 | `python start.py`（启动后端 8000）/ 前端 `npm run dev`（5173/5174） |
| 虚拟环境 | `.venv`（必须用 `.venv\Scripts\python.exe` 跑，系统 python 无 camel 依赖） |
| LLM | DeepSeek（`OPENAI_API_BASE_URL=https://api.deepseek.com/v1`，`OPENAI_MODEL_NAME=deepseek-chat`） |
| 数据 | 124 件真实刑事案例 `dataset/criminal_case_dataset.json` |
| 玩家模式 | `SIMLAW_PLAYER_LAWYER_MODE=defendant`（只支持辩护律师） |

**启动**：
```
后端: .venv\Scripts\python.exe -m uvicorn ws_server:app --host 127.0.0.1 --port 8000  (cwd=backend)
前端: frontend\node_modules\.bin\vite.cmd dev (cwd=frontend)
验证: .venv\Scripts\python.exe scripts\verify_criminal.py (cwd=backend)
```

---

## 三、刑事流程状态机（核心架构）

### 阶段码
```
接受委托 → 侦查阶段 → 审查起诉 → 辩护词起草 → 刑事一审 → 上诉决策 → 刑事二审 → 终审
    LC        INV         PR          DS          CR         CRA
```

### 状态机文件
- 事件总线：`backend/src/core/event_bus.py`（`EventType` 枚举 + 发布订阅）
- 状态机：`backend/src/orchestration/case_fsm.py`（`CaseState` 常量 + `VALID_TRANSITIONS` 迁移图 + `SHARED_CASE_STATES`）
- 编排器：`backend/src/orchestration/scenario_orchestrator.py`（订阅事件 → 调度场景，刑事 handler 在文件后段）
- 场景：`backend/src/scenarios/`（legal_consultation / investigation / prosecution_review / defense_opinion_drafting / criminal_trial / criminal_appeal_trial）

### 主链路（已验证正确）
```
空闲 → 等待前台接待 → 委托洽谈中(LC) → 侦查(INV) → 审查起诉(PR) → 起诉书已递交
  → 辩护词起草(DS) → 辩护词已递交 → 等待刑事一审 → 刑事一审(CR) → 刑事一审判决
  → [服判→已结案 | 上诉→刑事二审(CRA)] → 刑事终审判决 → 已结案
```
分支：侦查撤案、不起诉、判决生效均可提前结案。

### 角色
| 角色 | 说明 |
|------|------|
| 委托人（家属） | 刑事案由家属启动（plaintiff 角色位） |
| 被告人 | 犯罪嫌疑人/被告人（defendant 角色位） |
| 辩护律师 | 学生/玩家扮演对象 |
| 检察官 | 国家公诉人（prosecutor agent） |
| 侦查人员 | 公安侦查员（可选） |
| 法官 | 刑事审判长 |

### 刑事特有程序
取保候审申请、非法证据排除、认罪认罚从宽、被告人最后陈述、上诉/抗诉。

---

## 四、已完成的改造（本会话）

1. **纯刑事化**：删除民事场景（complaint/defense/court_investigation/appeal 等）、民事文书工具、民事事件类型、民事状态迁移、民事 pipeline.py
2. **保留通用工具**：search_laws（法条检索）、save_client_memory、save_lawyer_memory、技能加载、前台接待、地图编排
3. **玩家模式纯辩护律师**：`SIMLAW_PLAYER_LAWYER_MODE=defendant`，player_lawyer/agent.py 固定 party_role=defendant
4. **前台推荐显示中文**：推荐文本清洗 lawyer_id → 中文名；名册不暴露内部编号
5. **前台/委托洽谈分层注入案情**：
   - 前台(RECEPTION)：只注入"案情概览"（被羁押人+罪名+强制措施），当事人概括陈述来意
   - 委托洽谈(LC)：律师先听当事人分步陈述，不一次性注入完整案情/证据
6. **对话多行展示**：前端 DialogueFeed 改为完整对话历史滚动列表
7. **当事人显示中文名**：前端 agentDisplayName 优先后端下发中文名，不用精灵名（Molly 等）
8. **阶段工具权限（纯刑事）**：`stage_tool_manifest.yaml` 声明每阶段每角色工具（agent_type_defaults 常驻 + role_tools 阶段专属），`stage_tool_resolver.py` apply/clear 注入与清理，6 个阶段（LC/INV/PR/DS/CR/CRA）编排器均已接入；修复判决书工具名对齐（`draft_*_criminal_judgment_document`）
9. **教学评分模块（teaching/）**：rubrics（CJ-Bench 8 能力 + 阶段矩阵）+ transcript + scorer（LLM-as-judge → LearningEvent）+ 本地法条库 RAG + learner 画像 + report + routes + 阶段结束自动触发评分 + 即时法条校验（详见第五节）

---

## 五、教育教学整合方向（精学模块——已实现 ✅）

### 核心教学模型
学生作为辩护律师进入真实刑事案件 → AI 陪练（检察官/法官/当事人）质询 → 阶段结束后 LLM 裁判评分 → 找漏洞 + 法条溯源 → 画像/推荐。

### 已确认的评分决策（用户拍板 + 已实现）
1. **评分对象**：只评学生扮演的辩护律师发言
2. **评分时机**：整个阶段结束之后统一评分（异步线程，不阻塞流程）
3. **法条溯源**：本地法条库 RAG（零外部依赖），Dify/向量库留作增强
4. **评分框架**：采用 **CJ-Bench 8 能力刑法化**（非旧 5 维度草案）

### 评分框架（8 能力，唯一权威在 `teaching/rubrics.py`）
| 能力码 | 中文名 | 主考阶段 |
|--------|--------|---------|
| fact_identification | 事实识别 | LC/INV |
| rule_retrieval | 规范检索 | PR/DS |
| subsumption | **要件涵摄★** | PR/DS/CR/CRA（三栏表专项） |
| claim_construction | 辩护主张构建 | PR/DS/CRA |
| evidence_marshalling | 证据组织 | DS/CR |
| evidentiary_advocacy | 质证对抗 | CR/CRA |
| position_consistency | 立场一致性 | DS/CR/CRA |
| procedural_compliance | 程序合规 | LC/INV/PR/CR |

阶段 × 能力矩阵见 `teaching/rubrics.py::STAGE_CAPABILITY_MATRIX`（primary 权重 1.0 / secondary 0.5）。

### 教学模块文件（均已实现）
```
teaching/
├── __init__.py            # 导出 TeachingScorer 等
├── rubrics.py             # 8 能力 + 6 阶段矩阵 + judge 提示词 + 涵摄专项
├── law_corpus.py          # 本地法条检索/核验（search_law/verify_citation/resolve_article）
├── citation_check.py      # 即时法条校验（错误条号 + 相近法条建议）
├── transcript.py          # 学生发言 + 金标准组装
├── scorer.py              # LLM-as-judge → LearningEvent
├── learner.py             # 跨案件画像（sandbox_data/teaching/profiles/）
├── report.py              # 雷达图/成长曲线/知识缺口/按缺口推荐 quiz 题
├── routes.py              # /api/teaching/* 路由
└── knowledge_points.json  # 冷启动知识点（quiz_bank 86 个去重）
```

### API（已挂载 ws_server）
| 方法/路径 | 用途 |
|----------|------|
| POST `/api/teaching/score` | 手动触发评分（body: case_id, stage, student_id?） |
| GET `/api/teaching/event/{case_id}/{stage}` | 取单次 LearningEvent |
| GET `/api/teaching/profile/{student_id}` | 学习者画像 |
| GET `/api/teaching/report/{student_id}` | 报告 + 推荐 |
| GET `/api/teaching/corpus` | 法条库状态 |

### 触发链路
1. **即时校验**：`player_lawyer/routes.py::submit_response` 返回 `citation_feedback`（`SIMLAW_TEACHING_INSTANT_CITATION` 默认开，法条库缺失自动静默）
2. **阶段自动评分**：`scenario_orchestrator.py::_maybe_trigger_teaching_scoring` 在 LC/INV/PR/DS/CR/CRA 结束后异步触发（仅玩家模式 + 非 AI 代理）
3. 评分输入 = ledger `submissions` + `{stage}_result.json` dialog_history + 数据集金标准
4. 输出 → `case_output_dir/teaching/{stage}_learning_event.json` → 画像累计

### LearningEvent 输出结构（对齐星火智学）
```json
{
  "event_id": "evt_20260821_..._case_1_DS",
  "schema_version": "learning-event-v1",
  "student_id": "anonymous", "case_id": "case_1", "stage": "DS",
  "capability_scores": {"subsumption": {"score": 0.7, "raw": 7, "weight": 1.0, "rationale": "", "evidence_quote": ""}},
  "subsumption_table": [{"element": "非法占有目的", "fact_found": "", "conclusion": "符合|不符合|存疑", "comment": ""}],
  "knowledge_verdicts": [{"kp": "盗窃罪构成要件", "status": "mastered|partial|missing", "reason": ""}],
  "error_tags": ["法条引用错误-264与266混淆"],
  "law_citations": [{"citation": "《刑法》第二百六十四条", "status": "valid|invalid_article|invalid_title", "content": "…", "issue": ""}],
  "knowledge_gaps": ["盗窃罪构成要件"],
  "overall_feedback": "面向学生的第二人称反馈",
  "scored_at": "2026-08-21T17:22:29"
}
```

### 评分数据来源
- 学生发言：`{case_output_dir}/_player_lawyer/player_run_ledger.json`（submissions）
- AI 陪练对话：各阶段 `{case_output_dir}/{stage}_result.json` 的 dialog_history
- 金标准：`dataset/criminal_case_dataset.json`（guiding_points 仅 55/124，defense_hint 37/124，P7 未回填）

---

## 六、关键技术决策与现状

### Dify API（法条检索）
- URL: `http://121.46.5.115/v1`（Dify 风格 API）
- API Key: 见 `.env` 的 `DIFY_API_KEY`（不入库）
- **现状：接口测试不通**（nginx 404，Dify 未正确路由到 /v1；`/app/{id}/develop` 也不可访问）
- 已多次测试确认（2026-08）：`/v1/chat-messages`、`/v1/parameters`、`/v1/apps`、`/v1/messages`、`/console/api` 全部 404，80 端口只有 nginx 默认页
- **决策：先不阻塞主线**，Dify 接口做可插拔设计（`DifyCitationSource` 类），修好后切换

### 向量数据库选型
- **推荐 Qdrant**（轻量、Docker 单容器、支持过滤+标量检索、比赛演示稳定）
- 备选：ChromaDB（零部署嵌入式）、pgvector（需 SQLite→PG 迁移，成本高）
- **现状（2026-08）**：`backend/legal_corpus/processed/*.jsonl` **已就绪**——由 `scripts/build_law_corpus_from_pdfs.py` 从《刑法》《刑诉法》PDF（PyMuPDF 直读，无需转 TXT）构建：刑法 504 条 + 刑诉法 308 条，schema 与 `citation_check_tool` 对齐。教学模块用**本地词法 RAG**（`teaching/law_corpus.py`，n-gram 余弦 + 关键词融合，纯 stdlib，参考 legal-rag-poc），零外部依赖、无需 embedding API，离线可用。Qdrant 语义检索留作后续增强。

### 现有法条检索基础设施
- `law_retrieval_tool.py`：文件向量检索（DashScope embedding + 本地 .npy，`SIMLAW_ENABLE_LAW_RETRIEVAL=false` 默认关闭，索引缺失）
- `citation_check_tool.py`：本地法条引用校验（读取 `legal_corpus/processed/*.jsonl`，**现已可用**）
- `case_retrieval_tool.py`：类案检索（语料缺失）
- `teaching/law_corpus.py`：教学法条检索 + 引用核验（`search_law`/`verify_citation`/`resolve_article`）
- **元典 MCP**（`utils/yuandian_mcp_client.py` + `tools/legal/yuandian_law_tool.py`）：`.env` 已配 `YUANDIAN_API_KEY`，但依赖外网 `open.chineselaw.com`，演示环境不可靠；工具已注册常驻，调用失败返回错误文本不阻断

### RAG / 微调定位（对齐星火智学说明书）
- **RAG**：增强现有 `search_laws` 工具（精确匹配条号 + Qdrant 语义兜底 + Dify 权威核验），不改变案例流程
- **微调**：现阶段**不需要**。说明书明确"微调只优化教学话术，事实知识靠 RAG"。评分先用 prompt 工程，等 50+ 评分数据再考虑 LoRA 对比实验

---

## 七、星火智学产品对齐（参照文档要点）

产品文档：`7.27第二次资料收集整理\_md\01_产品架构.md`

### 四模块闭环
```
预习（知识地图/困惑/前测）
  → 精学（案件调查/证据/辩论）★当前
  → 复习（变式/角色互换/间隔复习）
  → 教师闭环（班级学情/干预）
```

### 精学智能体角色
| 角色 | 职责 | 禁止 |
|------|------|------|
| 案件导演 | 初始化案件、控制阶段、按权限揭示事实 | 不得生成未审核的标准答案 |
| 检索研究员 | 调用可信知识服务获得法条/案例 | 不得绕过 EvidencePack 自由引用 |
| AI对抗辩手 | 站在相反立场质询反驳学生 | 不得虚构事实或法源 |
| 证据审查员 | 检查事实、证据、引用一致性 | 不得代替法学审核确定争议结论 |
| 教学裁判/导师 | 按 Rubric 给形成性反馈 | 不得用于正式成绩全自动最终评定 |

### 核心数据对象
KnowledgeCard / CaseBundle / TaskItem / EvidencePack / LearningEvent / LearnerProfile / Recommendation

### 法条溯源设计（本地词法 RAG + Dify 可插拔）
```
学生发言
  ├─(A) 提取引用「刑法第264条」→ 精确匹配（teaching/law_corpus.verify_citation）
  ├─(B) 本地词法 RAG 语义兜底 → 学生意思对但条号写错（search_law 查漏/建议相近法条）
  ├─(C) Dify API（可插拔，接口修通后启用）→ 权威法条原文 + 时效核验
  └─(D) LLM裁判整合 → 引用是否正确 → error_tags / knowledge_gaps
```

---

## 八、未来改进方向（待办）

### P0（获奖核心）
- [x] `teaching/` 评分内核：rubrics + scorer + transcript（8 能力 CJ-Bench 刑法化，已打通全阶段评分，先评 DS 辩护词 + CR 庭审）
- [ ] 对抗质询增强：检察官/法官主动追问学生漏洞
- [x] LearningEvent 结构化记录（`teaching/{stage}_learning_event.json`）
- [x] 学习者画像（跨案件累计知识/能力/错误，`sandbox_data/teaching/profiles/`）
- [x] 法源核验：本地法条库（`legal_corpus/processed`：刑法 504 + 刑诉法 308）+ 本地词法 RAG（Dify 可插拔）

### P1（体验增强）
- [ ] 前端报告页（P8）：阶段角标"已批阅" + 抽屉（能力横条/subsumption 三栏表/error_tags/overall_feedback）+ 学期雷达图（内联 SVG）
- [ ] 即时法条校验前端警示 chip（`citation_feedback` 已从后端返回，前端未接 UI）
- [ ] AI 对抗辩手（站在反方质询逼学生补证）
- [ ] 复习/变式任务（错题归因、改一个关键事实重练）
- [ ] 教师看板（班级共性问题聚合）
- [ ] Dify API 对接（等接口修通）
- [ ] 金标准回填 `scripts/backfill_gold.py`（guiding_points 55/124、defense_hint 37/124 → 124/124）

### P2（展示加分）
- [ ] 数字人/语音（讯飞能力）
- [ ] 论证图/证据板/时间线可视化
- [ ] 微调对比实验（评分模型 LoRA）

### 需要确认的决策
1. 评分维度是否最终采用 8 维 CJ-Bench 刑法化框架（教学模块已实现，见 `teaching/rubrics.py`）
2. 法条库来源：内置 JSON ✓ / Dify（接口修通后增强）/ 两者
3. Qdrant 是否还要接（当前本地词法 RAG 已离线可用）
4. 评分报告已接入学习者画像 + 推荐任务（`/api/teaching/report`）

---

## 九、常用验证命令

```bash
# 后端整体验证（模块导入 + manifest + FSM + teaching）
cd backend && .venv\Scripts\python.exe scripts\verify_criminal.py

# 教学模块离线功能测试（rubrics/语料/检索/引用核验/假裁判评分/画像/报告）
cd backend && .venv\Scripts\python.exe -X utf8 scripts\test_teaching.py

# 构建本地法条库（《刑法》《刑诉法》PDF → legal_corpus/processed/*.jsonl）
cd backend && .venv\Scripts\python.exe scripts\build_law_corpus_from_pdfs.py

# 冷启动知识点（quiz_bank + 案例罪名 → src/teaching/knowledge_points.json）
cd backend && .venv\Scripts\python.exe scripts\build_knowledge_points.py

# 状态流转集成测试（临时脚本）
.venv\Scripts\python.exe C:\Users\Legion\AppData\Local\Temp\opencode\test_fsm_flow.py

# 前端类型检查 + 构建
frontend\node_modules\.bin\vue-tsc.cmd --noEmit -p frontend\tsconfig.json
frontend\node_modules\.bin\vite.cmd build frontend
```

---

*本文档由用户与 AI 协作整理，记录项目设计思路与决策，作为后续开发的项目记忆。*
