# LEGALWORLD 刑法版（纯刑事）

此项目是 [LEGALWORLD](https://github.com/chidaic/Legal-world.git) 的**纯刑事适配版本**——刑事公诉案件全流程 AI 仿真教学环境（委托洽谈 → 侦查 → 审查起诉 → 辩护词 → 一审 → 上诉 → 二审 → 终审）。

学生在 124 件真实刑事案件中扮演辩护律师，AI 扮演检察官/法官/当事人对抗；每次发言即时核验法条引用，阶段结束后按 8 能力框架自动批阅，跨案件累计学习者画像并沉淀补弱技能卡。

本项目已移除全部民事流程（起诉状/答辩状/民事一审二审/民事上诉等），仅保留刑事公诉流程与通用基础设施（法条检索、记忆工具、前台接待、地图编排等）。

## 教学能力

- **玩家辩护律师模式**：学生全程扮演辩护律师，六阶段（LC/INV/PR/DS/CR/CRA）完整走完
- **即时法条核验**：发言中的《刑法》《刑诉法》引用当场校验（条号存在性 + BM25 相近法条建议）
- **NLI 引用对齐**：CitaLaw 式三段论评估——验证"所引法条是否真的支撑该论断"（本地中文 cross-encoder + LLM 裁判双层裁决）
- **8 能力自动批阅**：CJ-Bench 刑法化框架（事实识别/规范检索/要件涵摄/主张构建/证据组织/质证对抗/立场一致/程序合规），其中规范检索为确定性公式分（可审计），其余 LLM-as-judge
- **三层学习报告**：即时警示 chip → 阶段批阅抽屉（能力横条/涵摄三栏表/引用对齐明细）→ 学期雷达档案（成长曲线/知识缺口/练习推荐）
- **技能卡闭环**：批阅发现的弱点自动沉淀为个人技能卡，下一局可查看并携带上场
- **辩护效果真实反馈**：审查起诉阶段辩护意见成立可促成不起诉提前结案；服判案件判决生效即结案

详见 `AGENTS.md`。

## 刑事流程

```
接受委托 → 侦查阶段 → 审查起诉 → 辩护词起草 → 刑事一审 → 上诉决策 → 刑事二审 → 终审
    LC        INV         PR          DS          CR         CRA
```

### 阶段码

| 阶段码 | 名称 | 说明 |
|--------|------|------|
| LC | 委托洽谈 | 律师与委托人家属洽谈，建立委托关系 |
| INV | 侦查阶段 | 律师会见嫌疑人、了解涉嫌罪名、申请取保候审 |
| PR | 审查起诉阶段 | 阅卷、会见被告人、向检察官提交辩护意见 |
| DS | 辩护词起草 | 收到起诉书后起草《辩护词》 |
| CR | 刑事一审庭审 | 公诉人 vs 辩护人对抗式庭审 |
| CRA | 刑事二审庭审 | 上诉后的二审终审 |

### 角色

| 角色 | 说明 |
|------|------|
| 委托人（家属） | 刑事案由家属启动，代为委托辩护律师 |
| 被告人 | 犯罪嫌疑人/被告人 |
| 辩护律师 | 维护被告人合法权益 |
| 检察官 | 国家公诉人 |
| 侦查人员 | 公安侦查员（可选） |
| 法官 | 刑事审判长 |

### 刑事特有程序

- 取保候审申请（侦查阶段）
- 非法证据排除（庭审质证）
- 认罪认罚从宽（审查起诉阶段）
- 被告人最后陈述（一审庭审）
- 上诉/抗诉（一审判决后）
- 不起诉提前结案（审查起诉阶段，辩护成功）

## 目录结构

```
Legal-world-criminal/
├── README.md
├── requirements.txt
├── start.py
├── dataset/                       # 刑事案例数据集
├── dataset_builder/               # 数据集构建工具
├── docs/
├── examples/
└── backend/
    ├── ws_server.py               # WebSocket 入口
    ├── sandbox_main.py
    ├── scripts/                   # 数据准备、迁移、验证脚本
    ├── legal-skillhub/
    │   └── public/legal/
    │       ├── client/memory/     # 当事人记忆
    │       └── lawyer/
    │           ├── memory/        # 律师记忆
    │           └── document-drafting/
    │               ├── lawyer-defense-opinion-drafting/   # ★ 刑事辩护词
    │               └── lawyer-criminal-appeal-drafting/   # ★ 刑事上诉状
    └── src/
        ├── agents/
        │   ├── base_agent.py
        │   ├── receptionist_agent.py    # 前台
        │   ├── client_agent.py          # 当事人
        │   ├── lawyer_agent.py          # 辩护律师
        │   ├── judge_agent.py           # 刑事法官
        │   ├── prosecutor_agent.py      # ★ 检察官/公诉人
        │   └── investigator_agent.py    # ★ 公安侦查员（可选）
        ├── scenarios/
        │   ├── base_scenario.py
        │   ├── legal_consultation.py    # 委托洽谈（刑事入口）
        │   ├── investigation.py         # ★ 侦查阶段
        │   ├── prosecution_review.py    # ★ 审查起诉阶段
        │   ├── defense_opinion_drafting.py  # ★ 辩护词起草
        │   ├── criminal_trial.py        # ★ 刑事一审
        │   └── criminal_appeal_trial.py # ★ 刑事二审
        ├── tools/
        │   ├── common/             # 通用工具（法条检索、技能加载等）
        │   ├── client/             # 当事人记忆工具
        │   └── legal/              # 刑事文书工具（起诉书/辩护词/公诉词/刑一刑二判决书）
        ├── pipeline/               # 阶段→工具分配（纯刑事 manifest）
        ├── orchestration/          # 状态机 / 编排引擎
        ├── player_lawyer/          # 玩家扮演辩护律师
        └── teaching/               # ★ 教学评分（8能力/引用核验/NLI对齐/画像/技能卡）
```

## 验证

```bash
cd backend
python scripts/verify_criminal.py
```

## 许可

本项目基于原始 [LEGALWORLD](https://github.com/chidaic/Legal-world.git) 项目，遵循相同的开源许可协议。
