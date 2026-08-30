# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

LEGALWORLD 刑法版（纯刑事）——刑事公诉案件全流程 AI 仿真教学环境（比赛项目"星火智学"）。学生扮演辩护律师走完刑事流程，AI 扮演检察官/法官/当事人对抗，阶段结束后 LLM-as-judge 按 8 能力框架评分。已移除全部民事流程。上游：https://github.com/chidaic/Legal-world.git（民事版）。

**范围（2026-08-26 拍板）**：星火智学产品采用模块化设计，本项目**只做精学模块**（智能体编排：流程仿真 + 多智能体对抗 + 教学评分）。预习、复习、教师闭环是并列独立模块，**均不在本项目范围内**——不实现、不集成、不预留。模块间仅约定数据接口（LearningEvent / LearnerProfile / 技能卡 schema 供外部消费）。

深入文档：`AGENTS.md`（项目全景记忆，opencode 会自动加载，Claude Code 亦可参考）。

## 常用命令

**必须用 `.venv` 里的 Python**（系统 python 无 camel 依赖）。Shell 为 Windows 上的 bash。

```bash
# 后端（cwd=backend，端口 8000）
../.venv/Scripts/python.exe -m uvicorn ws_server:app --host 127.0.0.1 --port 8000
# 或根目录：.venv/Scripts/python.exe start.py

# 前端（cwd=frontend，端口 5173，代理 /api 和 /ws 到 8000）
node_modules/.bin/vite dev

# 整体验证：模块导入 + stage manifest + FSM + teaching（不启 LLM，每次改动后跑）
cd backend && ../.venv/Scripts/python.exe scripts/verify_criminal.py

# 单元测试（pytest 离线套件：teaching/任务队列/file_io，26 用例，~5s）
cd backend && ../.venv/Scripts/python.exe -m pytest -q

# lint（ruff，规则集见根 pyproject.toml；存量债务项已带注释豁免）
cd backend && ../.venv/Scripts/python.exe -m ruff check src ws_server.py

# 前端 lint / 类型检查 / 构建
cd frontend && npm run lint
cd frontend && npm run typecheck
cd frontend && node_modules/.bin/vite build

# 沙箱重置（重测流程前必须做，否则 checkpoint 恢复旧状态）
cd backend && ../.venv/Scripts/python.exe scripts/reset_sandbox.py
```

测试框架为 **pytest**（`backend/tests/`，配置在根 `pyproject.toml`）：`conftest.py` 做环境隔离（禁 NLI 模型加载、profiles/skill-cards/scoring-db 指向 tmp），LLM 一律 Fake。`scripts/test_teaching.py` 是向后兼容 wrapper。CI 在 `.github/workflows/ci.yml`。打印中文的脚本必须 `-X utf8`（Windows GBK 控制台）。**所有新文件 UTF-8 编码写入。**

## 核心架构

### API 层 `src/api/`（2026-08-30 拆分，替代 4500 行 ws_server 巨石）

`ws_server.py` 仅 17 行薄入口（`from src.api import app`）。分层：**app_state**（进程单例，一律 `app_state.<name>` 访问，禁止 from-import 值拷贝）→ **runtime_issues / case_catalog / agent_status**（领域服务）→ **deps**（认证/DB/沙箱依赖）→ **player_gateway_admin / runtime_config / simulation_runtime** → **ws_endpoint + 6 组 \*_routes + lifecycle**。`create_app()` 在 `__init__.py` 组装。

**循环依赖处理惯例**：① 文件底部 import（bottom-import，如 agent_status ↔ runtime_issues）；② lazy trampoline 函数（如 `_initialize_runtime_state_lazily`，用于 agent_status → simulation_runtime）；③ 使用点函数内 import（`_get_sandbox_manager` 在 deps 的两处延迟引用）。**ruff I001 自动排序会重排 import 顺序、破坏加载次序敏感的环**——改 api 包 import 后必须跑 `import ws_server` 冒烟。

### 评分任务队列 `src/teaching/task_queue.py`

阶段结束的异步评分不再用 daemon 线程（进程退出即丢失）：SQLite 持久化任务表（WAL）+ ThreadPoolExecutor（默认 2 worker）+ 幂等键 `case::stage::student` + 崩溃恢复（stale running → pending）+ 失败重试（默认 3 次）。监控：`GET /api/teaching/scoring-tasks`，运维：`POST /api/teaching/scoring-tasks/retry-failed`。测试注入 runner + `SIMLAW_SCORING_DB_PATH` 隔离。

### 集中配置 `src/config.py`

pydantic-settings 分组（Teaching/Embedding/Database/Model），`get_settings()` 带 lru_cache（测试改 env 后需 `get_settings.cache_clear()`）。消除 law_embedding 硬编码云端点。旧模块的 `os.getenv` 渐进迁移，环境变量名不变。

### 自适应模块 `src/adaptive/`（EduBrain 融合）

学长 planner vendored 原样（`edubrain_planner.py` 黑盒，不魔改）；`service.py` 做题库缓存/判分/历史存储/精学画像 boost（missing +45 / partial +18 重排，打 case_weakness 标，不污染 evidence）。数据在 `backend/adaptive_data/`（30 题/Q矩阵/10 知识点）。模式：diagnostic（预习冷启动，无 case 信号）/ review（复习，带 boost）。历史 `sandbox_data/adaptive/{sid}/history.jsonl`（env `SIMLAW_ADAPTIVE_DATA_DIR` 隔离）。判分只在提交侧（plan 无答案防泄露）。前端 HeaderBar 三入口 + `AdaptiveQuiz.vue`（App.vue 的 activeView 切换，无 vue-router）。测试 `tests/test_adaptive.py`。

### 刑事流程状态机（理解一切的前提）

```
接受委托 → 侦查 → 审查起诉 → 辩护词起草 → 刑事一审 → 上诉决策 → 刑事二审 → 终审
   LC       INV      PR          DS          CR         CRA
```

事件驱动三层：
- `src/core/event_bus.py` — `EventType` 枚举 + 发布订阅
- `src/orchestration/case_fsm.py` — `CaseState` 常量（中文状态名）+ `VALID_TRANSITIONS` 迁移图 + `SHARED_CASE_STATES`
- `src/orchestration/scenario_orchestrator.py` — 订阅事件 → 查 AgentRegistry → 调度场景 handler（刑事 handler 在文件后段）→ 阶段结束触发教学评分 `_maybe_trigger_teaching_scoring`

场景实现在 `src/scenarios/`（legal_consultation / investigation / prosecution_review / defense_opinion_drafting / criminal_trial / criminal_appeal_trial）。**注意 `execute()` 签名不一致**：LC 系 async 直接 await；DS/CR/DD 系同步需 `asyncio.to_thread`。

### 阶段工具权限

`src/pipeline/stage_tool_manifest.yaml` 声明每阶段每角色可用工具（agent_type_defaults 常驻 + role_tools 阶段专属）；`stage_tool_resolver.py` 在编排器进入/退出阶段时 apply/clear。场景的每个参与角色（含 client/defendant）都必须在 role_tools 声明，否则报 "Role 'X' is not declared"。

### 玩家模式（教学核心）

`SIMLAW_PLAYER_LAWYER_MODE=defendant`（.env 已配，只支持辩护律师）。`src/player_lawyer/` 模块：`routes.py` 提供 REST（submit_response 即时返回 citation_feedback 法条校验）、`run_ledger.py` 记录学生提交、`agent.py` 固定 party_role=defendant。**PlayerLawyerAgent 是 prompt 透传管道**——塞给它的任何文本原样渲染到前端，给玩家的指令必须走 player_mode 分支（见 drafting_runtime）。

### 教学评分模块 `src/teaching/`

- `rubrics.py` — 8 能力 CJ-Bench 刑法化框架唯一权威（fact_identification / rule_retrieval / subsumption★要件涵摄 / claim_construction / evidence_marshalling / evidentiary_advocacy / position_consistency / procedural_compliance）+ 阶段×能力矩阵 STAGE_CAPABILITY_MATRIX + judge 提示词
- `scorer.py` — LLM-as-judge → LearningEvent（阶段结束经 ScoringTaskQueue 异步触发，不阻塞流程）
- `task_queue.py` — 评分任务持久化队列（见核心架构节）
- `law_corpus.py` — 本地法条检索/引用核验（`backend/legal_corpus/processed/*.jsonl`：刑法 504 条 + 刑诉法 308 条，n-gram 词法 RAG，零外部依赖）
- `learner.py` — 跨案件画像（`sandbox_data/teaching/profiles/`）；`report.py` — 雷达图/成长曲线/推荐
- `routes.py` — `/api/teaching/*`（score / event / profile / report / corpus）
- 评分输入：玩家 ledger submissions + `{stage}_result.json` dialog_history + `dataset/criminal_case_dataset.json` 金标准（124 案，guiding_points 仅 55/124 回填）

### 前端

Vue 3 + Vite + TS（`frontend/`），除 vue 外零运行时依赖。深色 "Judicial Archive" 卷宗审美，**不引入 UI 组件库、不重设计**；雷达图用内联 SVG 手绘（几何已定稿勿动）。WS 实时驱动（`lib/ws.ts`）+ REST（`lib/api.ts`）；playerMode 是内存态，刷新后靠 `/player-lawyer/runtime` 恢复。组件：StageRail（三态进度轨）、DialogueFeed（滚动对话）、StageReviewDrawer（阶段评分抽屉）、LearningDossier（学习档案）。

## 关键陷阱（改相关模块前先对照）

1. **EventType vs CaseState 命名撞车**：`DEFENSE_OPINION_FILED` 等是 CaseState 不是 EventType，订阅错则启动不报错但请求全 500。改 `EventType.X` 后全量扫描对照 `dir(EventType)`
2. **FSM 时序**：FSM handler priority=100 先于业务 handler；分流类设计的中间状态必须加分流目标出口边
3. **`VALID_TRANSITIONS` dict 重复键静默覆盖**：改迁移图后必须离线脚本遍历校验可达
4. **公诉案 plaintiff 必空**：party_info 只有 defendant/prosecutor（plaintiff 角色位是家属/委托人），按 plaintiff 查必空 → 委托人失控
5. **民事残留**：民事→刑事迁移时 grep "plaintiff"/"civil" 在玩家/角色路径的残留（曾吃掉玩家模式）
6. **重测前沙箱重置**：checkpoints 会恢复旧状态（见上方 reset_sandbox 命令）
7. **SHARED_CASE_STATES**：刑事阶段事件可能由 defendant 侧 client_path 发出，涉案状态必须加入否则双方 config 状态分裂
8. **import 名 grep 双侧**：延迟 import 的新名要 grep `class X` 确认位置（ws_server 曾 import 错包，请求时才炸）
9. **真实报错最快路径**：`GET /api/sandbox` 的 last_error，而非翻日志

## 环境与配置

- LLM：DeepSeek（`.env`: `OPENAI_API_BASE_URL=https://api.deepseek.com/v1`，`OPENAI_MODEL_NAME=deepseek-chat`）；camel-ai 框架驱动 agent
- 主要 flag（`src/utils/runtime_flags.py`）：`SIMLAW_PLAYER_LAWYER_MODE`、`SIMLAW_FRONTEND_MODE`（auto/legacy/player_v2，player 模式需 player_v2 WS 握手）、`SIMLAW_VERBOSE_SCENARIOS`、`SIMLAW_TEACHING_INSTANT_CITATION`
- 元典 MCP（yuandian）与 Dify 法条 API 均为可插拔外部依赖，不可用时不阻断主线；法条核验以本地 `legal_corpus` 为准
- `.env` 含真实密钥，已 untrack，勿提交
