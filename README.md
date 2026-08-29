# LEGALWORLD 刑法版（纯刑事）

[![CI](https://github.com/Richard110206/Legal-world-criminal/actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)
[![ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://docs.astral.sh/ruff/)
[![pytest](https://img.shields.io/badge/test-pytest-0a9edc.svg)](https://docs.pytest.org/)
[![python](https://img.shields.io/badge/python-3.11+-3776ab.svg)](https://www.python.org/)

此项目是 [LEGALWORLD](https://github.com/chidaic/Legal-world.git) 的**纯刑事适配版本**——刑事公诉案件全流程 AI 仿真教学环境（委托洽谈 → 侦查 → 审查起诉 → 辩护词 → 一审 → 上诉 → 二审 → 终审），"星火智学"（XH-202620）精学模块。

学生在 124 件真实刑事案件中扮演辩护律师，AI 扮演检察官/法官/当事人对抗；每次发言即时核验法条引用，阶段结束后按 8 能力框架自动批阅，跨案件累计学习者画像并沉淀补弱技能卡。

## 快速开始

### 环境要求

- Python 3.11+（必须使用项目 `.venv`，含 camel-ai 依赖）
- Node.js 20+（前端）
- `.env`（不入库）：`OPENAI_API_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL_NAME`（DeepSeek 等 OpenAI 兼容端点）

### 启动

```bash
# 后端（cwd=backend，端口 8000）
../.venv/Scripts/python.exe -m uvicorn ws_server:app --host 127.0.0.1 --port 8000

# 前端（cwd=frontend，端口 5173，代理 /api 与 /ws 到 8000）
npm install && npm run dev
```

### Docker 部署

```bash
cd backend && docker compose up -d   # postgres + backend（健康检查已配）
```

## 教学能力

- **玩家辩护律师模式**：学生全程扮演辩护律师，六阶段（LC/INV/PR/DS/CR/CRA）完整走完
- **即时法条核验**：发言中的《刑法》《刑诉法》引用当场校验（条号存在性 + BM25 相近法条建议）
- **NLI 引用对齐**：CitaLaw 式三段论评估——验证"所引法条是否真的支撑该论断"（本地中文 cross-encoder + LLM 裁判双层裁决）
- **8 能力自动批阅**：CJ-Bench 刑法化框架（事实识别/规范检索/要件涵摄/主张构建/证据组织/质证对抗/立场一致/程序合规），其中规范检索为确定性公式分（可审计），其余 LLM-as-judge
- **三层学习报告**：即时警示 chip → 阶段批阅抽屉 → 学期雷达档案（成长曲线/知识缺口/练习推荐）
- **技能卡闭环**：批阅弱点自动沉淀为个人技能卡，下一局可携带上场
- **辩护效果真实反馈**：审查起诉阶段辩护意见成立可促成不起诉提前结案

## 刑事流程

```
接受委托 → 侦查阶段 → 审查起诉 → 辩护词起草 → 刑事一审 → 上诉决策 → 刑事二审 → 终审
    LC        INV         PR          DS          CR         CRA
```

| 阶段码 | 名称 | 说明 |
|--------|------|------|
| LC | 委托洽谈 | 律师与委托人家属洽谈，建立委托关系 |
| INV | 侦查阶段 | 会见嫌疑人、了解涉嫌罪名、申请取保候审 |
| PR | 审查起诉 | 阅卷、会见被告人、向检察官提交辩护意见 |
| DS | 辩护词起草 | 收到起诉书后起草《辩护词》 |
| CR | 刑事一审 | 公诉人 vs 辩护人对抗式庭审 |
| CRA | 刑事二审 | 上诉后的二审终审 |

角色：委托人（家属）/ 被告人 / 辩护律师（学生扮演）/ 检察官 / 法官 / 侦查人员（可选）。刑事特有程序：取保候审、非法证据排除、认罪认罚从宽、最后陈述、上诉/抗诉、不起诉提前结案。

## 架构

```
backend/
├── ws_server.py               # 薄入口（uvicorn ws_server:app，向后兼容）
├── src/
│   ├── api/                   # ★ API 层（模块化路由 + 集中状态）
│   │   ├── __init__.py        #    create_app() 工厂 + 子系统挂载
│   │   ├── app_state.py       #    进程级单例与路径常量（app_state.<name> 访问）
│   │   ├── deps.py            #    认证/DB/沙箱解析依赖
│   │   ├── schemas.py         #    请求模型
│   │   ├── auth_routes.py / sandbox_routes.py / simulation_routes.py
│   │   ├── system_routes.py / debug_routes.py
│   │   ├── ws_endpoint.py     #    /ws WebSocket 端点
│   │   ├── agent_status.py / case_catalog.py / simulation_runtime.py
│   │   ├── player_gateway_admin.py / runtime_issues.py / runtime_config.py
│   │   └── lifecycle.py       #    startup/shutdown 钩子
│   ├── agents/                # 角色智能体（律师/检察官/法官/侦查员…）
│   ├── scenarios/             # 六阶段场景实现
│   ├── orchestration/         # 事件总线 + 案件状态机 + 编排器
│   ├── teaching/              # ★ 教学评分管线（见下）
│   ├── player_lawyer/         # 玩家扮演辩护律师子系统
│   ├── pipeline/              # 阶段→工具权限 manifest
│   └── config.py              # ★ pydantic-settings 集中配置
├── tests/                     # ★ pytest 离线测试套件（NLI 隔离）
├── legal_corpus/processed/    # 本地法条库（刑法 504 + 刑诉法 308，BM25/BM25F）
└── scripts/                   # 数据构建/验证/回填脚本
```

### 教学评分管线（teaching/）

```
学生发言 → ① 规则层（法条引用存在性） → ② NLI 层（引用三段论对齐）
        → ③ LLM-as-judge（8 能力 rubric） → ④ 确定性覆盖（rule_retrieval 公式分）
        → LearningEvent（learning-event-v1）→ 学习者画像 → 技能卡
```

评分任务经 **ScoringTaskQueue**（`teaching/task_queue.py`）执行：SQLite 持久化 + 有界线程池 + 幂等提交 + 崩溃恢复 + 失败重试，`GET /api/teaching/scoring-tasks` 可监控。

### 主要 API

| 方法/路径 | 用途 |
|-----------|------|
| `WS /ws` | 前端实时通道（认证 + 模拟事件流） |
| `POST /api/auth/*` | 注册 / 登录 / 刷新 |
| `POST /api/sandbox/start` · `GET /api/sandbox` | 沙箱生命周期 |
| `GET /api/sandbox/cases` | 案件目录（picker 元数据） |
| `POST /api/sandbox/player-lawyer/*` | 玩家提交发言 / 文书起草（即时 citation_feedback） |
| `POST /api/teaching/score` | 手动触发阶段评分 |
| `GET /api/teaching/event/{case}/{stage}` | 单次 LearningEvent |
| `GET /api/teaching/profile/{student}` | 学习者画像 |
| `GET /api/teaching/report/{student}` | 学期报告 + 推荐 + 技能卡 |
| `GET /api/teaching/scoring-tasks` | 评分队列监控 |

## 开发

```bash
# 后端 lint（ruff，配置见根 pyproject.toml）
cd backend && ../.venv/Scripts/python.exe -m ruff check src ws_server.py

# 后端测试（pytest 离线套件：teaching + 任务队列 + file_io）
cd backend && ../.venv/Scripts/python.exe -m pytest -q

# 后端整体验证（模块导入 + manifest + FSM + teaching）
cd backend && ../.venv/Scripts/python.exe -X utf8 scripts/verify_criminal.py

# 前端
cd frontend && npm run lint && npm run typecheck && npm run build
```

CI（`.github/workflows/ci.yml`）在 push/PR 时自动执行：ruff → 应用装配冒烟（55 路由断言）→ pytest → verify_criminal → vue-tsc + vite build。

配置经 `src/config.py`（pydantic-settings）集中管理：embedding 端点、NLI 开关、评分队列参数、教学数据目录、数据库连接。环境变量名不变，`.env` 继续生效。

## 数据

- 124 件真实刑事案例全量金标准：`dataset/criminal_case_dataset.json`
- 本地法条库：`backend/legal_corpus/processed/*.jsonl`（《刑法》《刑诉法》PDF 构建）

## 许可

本项目基于原始 [LEGALWORLD](https://github.com/chidaic/Legal-world.git) 项目，遵循相同的开源许可协议。
