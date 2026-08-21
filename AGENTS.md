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

---

## 五、教育教学整合方向（精学模块——当前主攻）

### 核心教学模型
学生作为辩护律师进入真实刑事案件 → AI 陪练（检察官/法官/当事人）质询 → 阶段结束后 LLM 裁判评分 → 找漏洞 + 法条溯源 → 画像/推荐。

### 已确认的评分决策（用户拍板）
1. **评分对象**：只评学生扮演的辩护律师发言
2. **评分时机**：整个阶段结束之后统一评分
3. **法条溯源**：需要向量索引数据库 + DFF API（法条检索）

### 评分 5 维度（草案，待最终确认）
| 维度 | 考察点 |
|------|--------|
| fact_identification 事实识别 | 是否抓住定罪/量刑关键事实 |
| rule_retrieval 规则检索 | 罪名构成要件、法条引用是否准确 |
| subsumption 要件涵摄 | 事实↔构成要件对应是否正确 |
| counter_argument 对抗论证 | 能否回应质询、抓住漏洞 |
| procedural 程序合规 | 刑事程序（会见/取保/质证/最后陈述） |

### LearningEvent 输出结构（对齐星火智学）
```json
{
  "student_id": "anonymous_023",
  "case_id": "case_1",
  "stage": "CR",
  "ability_scores": {"fact_identification": 0.72, "rule_retrieval": 0.88, ...},
  "error_tags": ["法条引用错误-264与266混淆", "遗漏正当防卫时间条件"],
  "law_citations": [{"引用": "刑法第264条", "核验": "valid", "issue": ""}],
  "knowledge_gaps": ["盗窃罪构成要件"],
  "feedback": "你在XX环节漏掉了..."
}
```

### 评分数据来源
- 学生发言：`run_ledger.py`（PlayerInputGateway 落盘）
- AI 陪练对话：各阶段 `*_result.json` 的 dialog_history
- 金标准：数据集 `guiding_points`（裁判要点）、`defense_hint`（辩护提示）、各 stage 字段

### 待开发 teaching 模块（规划）
```
teaching/
├── rubrics.py          # 评分维度定义
├── scorer.py           # LLM裁判：找漏洞+溯源+打分（复用 eval_pipeline 的 LLM-as-judge 框架）
├── transcript.py       # 组装评分输入
├── citation_check.py   # 法源核验（本地法条库 + Qdrant + DFF 可插拔）
├── learner.py          # 学习者画像
├── report.py           # 报告/雷达图/成长曲线
└── routes.py           # API
```

---

## 六、关键技术决策与现状

### DFF API（法条检索）
- URL: `http://121.46.5.115/v1`（Dify 风格 API）
- API Key: `app-KcKmhii3cv1mZ8crSm30b31e`
- **现状：接口测试不通**（nginx 404，Dify 未正确路由到 /v1；`/app/{id}/develop` 也不可访问）
- 已多次测试确认（2026-08）：`/v1/chat-messages`、`/v1/parameters`、`/v1/apps`、`/v1/messages`、`/console/api` 全部 404，80 端口只有 nginx 默认页
- **决策：先不阻塞主线**，DFF 接口做可插拔设计（`DifyCitationSource` 类），修好后切换

### 向量数据库选型
- **推荐 Qdrant**（轻量、Docker 单容器、支持过滤+标量检索、比赛演示稳定）
- 备选：ChromaDB（零部署嵌入式）、pgvector（需 SQLite→PG 迁移，成本高）
- 现状：本地 `legal_corpus` 目录**不存在**，法条语料需准备（刑法全文/刑诉法/司法解释）

### 现有法条检索基础设施
- `law_retrieval_tool.py`：文件向量检索（DashScope embedding + 本地 .npy，`SIMLAW_ENABLE_LAW_RETRIEVAL=false` 默认关闭）
- `citation_check_tool.py`：本地法条引用校验（读取 `legal_corpus/processed/*.jsonl`）
- `case_retrieval_tool.py`：类案检索

### RAG / 微调定位（对齐星火智学说明书）
- **RAG**：增强现有 `search_laws` 工具（精确匹配条号 + Qdrant 语义兜底 + DFF 权威核验），不改变案例流程
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

### 法条溯源设计（Dify/向量库分工）
```
学生发言
  ├─(A) 提取引用「刑法第264条」→ 精确匹配
  ├─(B) Qdrant 语义检索 → 学生意思对但没写条号（查漏）
  ├─(C) DFF API → 权威法条原文 + 时效核验
  └─(D) LLM裁判整合 → 引用是否正确 → error_tags / knowledge_gaps
```

---

## 八、未来改进方向（待办）

### P0（获奖核心）
- [ ] `teaching/` 评分内核：rubrics + scorer + transcript（先打通 DS 辩护词 + CR 庭审）
- [ ] 对抗质询增强：检察官/法官主动追问学生漏洞
- [ ] LearningEvent 结构化记录
- [ ] 学习者画像（跨案件累计知识/能力/错误）
- [ ] 法源核验：本地法条库 + Qdrant（DFF 可插拔）

### P1（体验增强）
- [ ] AI 对抗辩手（站在反方质询逼学生补证）
- [ ] 复习/变式任务（错题归因、改一个关键事实重练）
- [ ] 教师看板（班级共性问题聚合）
- [ ] DFF API 对接（等接口修通）

### P2（展示加分）
- [ ] 数字人/语音（讯飞能力）
- [ ] 论证图/证据板/时间线可视化
- [ ] 微调对比实验（评分模型 LoRA）

### 需要确认的决策
1. 评分维度是否最终采用 5 维（事实识别/规则检索/要件涵摄/对抗论证/程序合规）
2. 法条库来源：内置 JSON / DFF / 两者
3. Qdrant 用 Docker 还是现有向量库环境
4. 评分报告是否要接入学习者画像 + 推荐任务

---

## 九、常用验证命令

```bash
# 后端整体验证（模块导入 + manifest + FSM）
cd backend && .venv\Scripts\python.exe scripts\verify_criminal.py

# 状态流转集成测试（临时脚本）
.venv\Scripts\python.exe C:\Users\Legion\AppData\Local\Temp\opencode\test_fsm_flow.py

# 前端类型检查 + 构建
frontend\node_modules\.bin\vue-tsc.cmd --noEmit -p frontend\tsconfig.json
frontend\node_modules\.bin\vite.cmd build frontend
```

---

*本文档由用户与 AI 协作整理，记录项目设计思路与决策，作为后续开发的项目记忆。*
