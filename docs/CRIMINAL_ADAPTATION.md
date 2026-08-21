# LEGALWORLD 刑法适配：设计思路与实现说明

> **当前项目状态**：本仓库现已改造为**纯刑事版本**。所有民事流程（起诉状/答辩状/民事一二审/民事上诉等）、民事场景文件、民事文书工具均已被移除，仅保留刑事公诉全流程与通用基础设施（法条检索、记忆工具、前台接待、地图编排等）。以下文档保留了原始"刑法适配"的设计过程作为历史参考。

## 一、原始项目架构理解

在动手修改之前，首先要理解原始 LEGALWORLD 的**编排哲学**。它不是简单的"LLM 写文书 → 输出"，而是模拟了一个**法律 AI 小镇**：多个 Agent（律师、当事人、法官）在事件总线的驱动下，按状态机规定的合法流程，协作完成一个民事案件的全生命周期。

核心架构由四个相互解耦的层次组成：

```
┌──────────────────────────────────────────────────┐
│                  LegalPipeline                   │  ← 编排层：控制"谁在什么时候干什么"
├──────────────────────────────────────────────────┤
│  Agent ← Scenario ← Skill ← Tool                │  ← 执行层：每个阶段是一个 Scenario
├──────────────────────────────────────────────────┤
│  EventBus ← CaseStateMachine                     │  ← 事件层：事件驱动状态流转
├──────────────────────────────────────────────────┤
│  stage_tool_manifest → registry → resolver       │  ← 配置层：声明式工具分配
└──────────────────────────────────────────────────┘
```

这四个层次的关系是：

1. **配置层**（manifest + registry）**声明**了"什么工具在什么阶段分配给什么角色"
2. **事件层**（EventBus + FSM）**驱动**案件状态在阶段之间合法流动
3. **编排层**（Pipeline）**读取**配置、**监听**事件，按状态机的路径**调度** Agent
4. **执行层**（Scenario）在每个阶段内，由 LLM Agent 根据 SKILL.md 指令与当事人对话、起草文书

理解了这个分层之后，做刑法适配就不再是零散地"加几个文件"，而是在每一层做对应扩展。

---

## 二、核心适配思路：不推翻，只扩展

### 2.1 设计原则

原始项目的工具、场景、Agent 都是为民事诉讼设计的——原告、被告、起诉状、答辩状。刑事流程的最大区别在于：

| 维度 | 民事 | 刑事 |
|------|------|------|
| 启动方 | 原告主动起诉 | 检察院公诉（国家追诉） |
| 律师角色 | 原告律师 / 被告律师 | 辩护律师（始终为被告方） |
| 核心对抗 | 原告 vs 被告 | 公诉人 vs 辩护人 |
| 审前程序 | 无 | 侦查 → 审查起诉 |
| 判决内容 | 赔偿金额、责任划分 | 刑种、刑期、罚金 |

基于这些差异，适配策略是**在原始架构的每个扩展点上做加法**：

- 新增 5 个刑事阶段（INV / PR / DS / CR / CRA），民事阶段保持不变
- 新增 2 个 Agent（检察官、侦查员），民事 Agent 保持不变
- 新增 5 个文书工具（起诉书、辩护词、公诉词、刑一判决、刑二判决），民事文书工具保持不变
- 新增 1 个 agent type（"prosecutor"），扩展现有的 agent type 系统

### 2.2 新增文件与修改文件的边界

新增文件（独立存在，不修改原文件）：

```
tools/legal/
├── indictment_drafting_tool.py                     # 起诉书工具
├── defense_opinion_drafting_tool.py                # 辩护词工具（核心）
├── public_prosecution_tool.py                      # 公诉词工具
├── criminal_first_instance_judgment_drafting_tool.py
└── criminal_second_instance_judgment_drafting_tool.py

agents/
├── prosecutor_agent.py                             # 检察官 Agent
└── investigator_agent.py                           # 侦查员 Agent（可选）

scenarios/
├── investigation.py                                # 侦查阶段场景
└── prosecution_review.py                           # 审查起诉场景

legal-skillhub/.../
├── lawyer-defense-opinion-drafting/SKILL.md        # 辩护词起草指令
└── lawyer-criminal-appeal-drafting/SKILL.md        # 刑事上诉状指令
```

需要修改的原有文件（合并式扩展）：

```
tools/legal/__init__.py          → 追加 5 个刑事工具的导出
tools/__init__.py                → 追加刑事工具的上层导出
agents/__init__.py               → 追加 ProsecutorAgent, InvestigatorAgent
scenarios/__init__.py            → 追加 InvestigationScenario, ProsecutionReviewScenario
pipeline/stage_tool_manifest.yaml → 追加刑事阶段和工具分配
pipeline/stage_tool_registry.py   → 追加 5 个刑事工具工厂函数
pipeline/stage_tool_resolver.py   → 追加刑事阶段码、agent type、角色名、推断逻辑
orchestration/case_fsm.py        → 追加刑事 CaseState 和 VALID_TRANSITIONS
core/event_bus.py                → 追加刑事 EventType 枚举值
player_lawyer/agent.py           → 扩展支持 defendant 模式
```

---

## 三、工具设计：三段式模式

### 3.1 为什么用三段式

原始项目的所有文书工具都遵循同一个三段式结构。这不是风格偏好，而是架构需要：

```
Schema（OpenAI Function Schema）
   ↓ 告诉 LLM "这个工具叫什么、接收什么参数"
Class（工具实现类）
   ↓ 实际的 PDF 渲染逻辑，与 LLM 无关
Factory（CAMEL FunctionTool 工厂函数）
   ↓ 把 Schema + Class 打包成 camel 框架可调用的 FunctionTool 对象
```

为什么要分成三段？因为工具在运行时是被**解耦注册**的：

1. `stage_tool_registry.py` 通过工厂函数 `create_xxx_tool(agent)` 实例化工具
2. `stage_tool_resolver.py` 根据 manifest 决定哪些 Agent 拿到哪些工具
3. Agent 拿到的是 `FunctionTool` 对象，LLM 调用时触发 Class 中的方法

### 3.2 以辩护词工具为例

下面以 `defense_opinion_drafting_tool.py` 为例，逐段说明每个部分的设计意图。

**第一段：OpenAI Function Schema**

```python
# 工具元数据 —— 与民事工具的命名风格保持一致
DEFENSE_OPINION_TOOL_NAME = "draft_defense_opinion_document"
DEFENSE_OPINION_DOCUMENT_TYPE = "defense_opinion"
DEFENSE_OPINION_RESULT_FIELD = "defense_opinion_text"
DEFENSE_OPINION_PDF_FILENAME = "DO_document.pdf"

def _build_schema() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": DEFENSE_OPINION_TOOL_NAME,
            "description": (
                "接收辩护律师已经写好的《辩护词》全文，生成 PDF 文件。"
                "工具本身不负责起草正文，只返回 document_type 和 pdf_path。"
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "document_text": {
                        "type": "string",
                        "description": "辩护律师已经写好的完整《辩护词》正文。",
                    }
                },
                "required": ["document_text"],
                "additionalProperties": False,
            },
        },
    }
```

这里有两个关键设计决策：

- **工具只做 PDF 渲染，不起草正文。** LLM 根据 SKILL.md 指令写出正文后，调用工具导出 PDF。这样工具本身不需要任何 AI 能力，是一个纯渲染器。
- **`strict: True`** 确保 LLM 必须传入 `document_text`，不能跳过或编造参数。

**第二段：工具实现类**

```python
class DefenseOpinionDraftingTool:
    """Render one defense opinion PDF from lawyer-authored text."""

    def __init__(self, agent: Any) -> None:
        self.agent = agent  # 持有 Agent 引用，用于获取案件输出目录

    def resolve_case_output_dir(self) -> Path:
        """从 agent.scenario_data 中解析案件输出目录。"""
        scenario_data = getattr(self.agent, "scenario_data", {}) or {}
        explicit = str(scenario_data.get("case_output_dir", "") or "").strip()
        if explicit:
            path = Path(explicit).resolve()
            path.mkdir(parents=True, exist_ok=True)
            return path
        return Path.cwd().resolve()

    def draft_defense_opinion_document(self, document_text: str) -> str:
        """接收辩护词正文，渲染 PDF 并返回 JSON payload。"""
        normalized_text = _normalize_text(document_text)
        if not normalized_text:
            raise ValueError("document_text is required.")

        pdf_path = ""
        try:
            resolved_pdf_path = (
                self.resolve_case_output_dir() / DEFENSE_OPINION_PDF_FILENAME
            )
            _render_pdf(normalized_text, resolved_pdf_path)
            pdf_path = str(resolved_pdf_path)
        except Exception as exc:
            logger.error("Failed to render defense opinion PDF: %s", exc)

        payload = {
            "document_type": DEFENSE_OPINION_DOCUMENT_TYPE,  # "defense_opinion"
            "document_text": normalized_text,
            "pdf_path": pdf_path,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
```

`resolve_case_output_dir()` 通过持有 `agent` 引用来定位案件输出目录——这是原始项目的一贯模式：工具不自己管理路径，而是从 Agent 身上读取上下文。PDF 渲染失败不会抛异常阻塞流程（`pdf_path` 留空），因为文书正文仍然保留在返回值中。

**第三段：CAMEL FunctionTool 工厂**

```python
def create_defense_opinion_drafting_tool(agent: Any) -> FunctionTool:
    impl = DefenseOpinionDraftingTool(agent)
    return FunctionTool(
        impl.draft_defense_opinion_document,
        openai_tool_schema=_build_schema(),
    )
```

这个工厂函数是 **注册表（stage_tool_registry.py）的调用入口**。注册表里只存函数引用，运行时传 agent 进来实例化：

```python
# stage_tool_registry.py 中的注册条目
REGISTERED_STAGE_TOOL_FACTORIES: dict[str, ToolFactory] = {
    # ... 民事工具 ...
    "draft_defense_opinion_document": create_defense_opinion_drafting_tool,
    # ... 其他刑事工具 ...
}
```

### 3.3 刑事 vs 民事工具的关键差异

与民事 `complaint_drafting_tool.py` 相比，刑事文书工具有两个实质差异：

1. **PDF 文件名不同：** `DO_document.pdf`（辩护词）vs `CD_document.pdf`（起诉状）。这不是随便起的——原始项目的文件名缩写对应阶段码：CD = Complaint Drafting, DO = Defense Opinion。
2. **返回的 `document_type` 不同：** `"defense_opinion"` vs `"complaint"`。这个值被下游的 `document_drafting_registry.py` 用于跨阶段文书传递（例如一二审之间传递上一审的判决书）。

除此之外，PDF 渲染逻辑（ReportLab + 宋体 CID 字体 + SimpleDocTemplate）与民事工具完全一致——这正是刻意保持的"同构性"，确保刑事工具可以直接复用已有的 document_drafting_support 和 judgment_drafting_registry。

---

## 四、流水线编排：Manifest → Registry → Resolver 三件套

### 4.1 设计意图

原始项目没有把"哪个阶段给哪个 Agent 什么工具"硬编码在代码里，而是用了一个 YAML 配置文件来声明。这样做的好处是：加一个新阶段或新工具时，只需要改配置，不需要改编排代码。

三个文件的分工：

```
stage_tool_manifest.yaml     ← "声明"：什么阶段有什么角色、用什么工具
stage_tool_registry.py       ← "注册"：每个工具 ID 对应的工厂函数
stage_tool_resolver.py       ← "解析"：运行时根据 manifest + registry 给 Agent 装配工具
```

### 4.2 Manifest 的刑事扩展

在 `stage_tool_manifest.yaml` 中，新增内容分为三层：

**第一层：工具注册引用（tool_registry_refs）**
```yaml
tool_registry_refs:
  # ── 民事工具（保持）──
  - search_laws
  - save_client_memory
  - save_lawyer_memory
  - draft_complaint_document
  # ... 其他民事工具 ...
  # ── 刑事工具（新增）──
  - draft_indictment_document
  - draft_defense_opinion_document
  - draft_public_prosecution_document
  - draft_first_instance_criminal_judgment
  - draft_second_instance_criminal_judgment
```

**第二层：Agent 类型默认工具（agent_type_defaults）**
```yaml
agent_type_defaults:
  lawyer: [search_laws, save_lawyer_memory]
  client: [save_client_memory]
  judge: [search_laws]
  prosecutor: [search_laws]          # ← 新增：检察官也需要法条检索
  receptionist: []
```

**第三层：阶段 → 角色 → 工具映射（stages）**
```yaml
stages:
  # ... 民事阶段保持不变 ...

  # 审查起诉阶段：律师阅卷，检察官可以起草起诉书
  PR:
    shared_tools: []
    role_tools:
      lawyer: []
      prosecutor:
        - draft_indictment_document
      defendant: []

  # 辩护词起草阶段：律师收到起诉书后起草辩护词
  DS:
    shared_tools: []
    role_tools:
      lawyer:
        - draft_defense_opinion_document
      defendant: []

  # 刑事一审庭审：法官写判决书，检察官发表公诉词
  CR:
    shared_tools: []
    role_tools:
      judge:
        - draft_first_instance_criminal_judgment
      prosecutor:
        - draft_public_prosecution_document
      defendant: []
      defense_lawyer: []
```

这里有一个重要细节：**manifest 中声明了什么角色（role）决定了 `infer_stage_role_name()` 如何把 Agent 映射到场景角色**。例如 CR 阶段声明了 `defense_lawyer`，那么在 `stage_tool_resolver.py` 中，律师 agent 在 CR 阶段会被推断为 `defense_lawyer` 而非普通的 `lawyer`。

### 4.3 Resolver 的角色推断逻辑

`infer_stage_role_name()` 是运行时最关键的方法——它决定了每个 Agent 在特定阶段被当作什么角色。刑事新增的推断逻辑：

```python
def infer_stage_role_name(stage_code: str, agent: Any) -> str:
    # ... 民事逻辑保持不变 ...

    # 侦查阶段：律师就是律师，client 类型的 Agent 视为侦查员
    if normalized_stage_code == "INV":
        if agent_type == "lawyer":
            return "lawyer"
        if agent_type == "client":
            return "investigator"

    # 审查起诉阶段：新增 prosecutor 类型 → "prosecutor" 角色
    if normalized_stage_code == "PR":
        if agent_type == "prosecutor":
            return "prosecutor"

    # 刑事庭审阶段：律师在法庭上是"辩护律师"
    if normalized_stage_code == "CR":
        if agent_type == "lawyer":
            return "defense_lawyer"  # 区别于民事的 plaintiff_lawyer/defendant_lawyer
```

这种按阶段码分派的模式确保了：同一个 LawyerAgent，在 CD（民事起诉状起草）阶段被当作"原告律师"，在 DS（辩护词起草）阶段被当作"辩护律师"——Agent 本身不知道也不关心自己的角色标签，角色由 resolver 根据阶段动态注入。

---

## 五、状态机设计：从民事流程到刑事流程

### 5.1 CaseState 的扩展方式

原始 `case_fsm.py` 中的 `CaseState` 类是一个**纯常量类**——每个状态是一个类属性，值是对应的中文标签。这种设计的好处是：

- 状态之间没有继承关系，是扁平的枚举
- FSM 的 `VALID_TRANSITIONS` 字典用这些常量做 key，形成一张**有向图**

刑事扩展直接在 `CaseState` 类中追加 14 个新状态：

```python
class CaseState:
    # ... 原始民事状态（保持不变）...

    # ── 刑事阶段（新增）──
    INVESTIGATION = "侦查阶段"
    PROSECUTION_REVIEW = "审查起诉阶段"
    DEFENSE_OPINION_DRAFTING = "辩护词起草中"
    DEFENSE_OPINION_FILED = "辩护词已递交"
    INDICTMENT_FILED = "起诉书已递交"
    WAITING_FOR_CRIMINAL_TRIAL = "等待刑事一审开庭"
    CRIMINAL_TRIAL_FIRST_INSTANCE = "刑事一审庭审中"
    CRIMINAL_FIRST_INSTANCE_VERDICT = "刑事一审判决"
    CRIMINAL_APPEAL_DECISION = "刑事上诉决策中"
    CRIMINAL_APPEAL_DRAFTING = "刑事上诉状起草中"
    CRIMINAL_APPEAL_FILED = "刑事上诉状已递交"
    WAITING_FOR_CRIMINAL_SECOND_TRIAL = "等待刑事二审开庭"
    CRIMINAL_TRIAL_SECOND_INSTANCE = "刑事二审庭审中"
    CRIMINAL_FINAL_VERDICT = "刑事终审判决"
```

### 5.2 VALID_TRANSITIONS：刑事流程的有向图

`VALID_TRANSITIONS` 本质上是一张**邻接表**，定义了哪些状态转换是合法的。刑事流程的新增路径：

```python
VALID_TRANSITIONS = {
    # ... 民事迁移（保持不变）...

    # ── 刑事流程 ──
    # 侦查 → 审查起诉（律师会见后，案件移送检察院）
    CaseState.INVESTIGATION: {CaseState.PROSECUTION_REVIEW},

    # 审查起诉 → 两条路：
    #   1. 检察官完成起诉书 → INDICTMENT_FILED
    #   2. 律师直接开始起草辩护词（不等待起诉书先递交）
    CaseState.PROSECUTION_REVIEW: {
        CaseState.INDICTMENT_FILED,
        CaseState.DEFENSE_OPINION_DRAFTING,
    },

    # 起诉书递交后 → 律师起草辩护词
    CaseState.INDICTMENT_FILED: {CaseState.DEFENSE_OPINION_DRAFTING},

    # 辩护词递交 → 等待开庭 → 一审庭审 → 一审判决
    CaseState.DEFENSE_OPINION_DRAFTING: {CaseState.DEFENSE_OPINION_FILED},
    CaseState.DEFENSE_OPINION_FILED: {CaseState.WAITING_FOR_CRIMINAL_TRIAL},
    CaseState.WAITING_FOR_CRIMINAL_TRIAL: {CaseState.CRIMINAL_TRIAL_FIRST_INSTANCE},
    CaseState.CRIMINAL_TRIAL_FIRST_INSTANCE: {CaseState.CRIMINAL_FIRST_INSTANCE_VERDICT},

    # 一审判决后 → 上诉决策（服判 → 结案，不服 → 上诉）
    CaseState.CRIMINAL_FIRST_INSTANCE_VERDICT: {CaseState.CRIMINAL_APPEAL_DECISION},
    CaseState.CRIMINAL_APPEAL_DECISION: {
        CaseState.CLOSED,
        CaseState.CRIMINAL_APPEAL_DRAFTING,
    },

    # 上诉 → 二审庭审 → 终审 → 结案
    CaseState.CRIMINAL_APPEAL_DRAFTING: {CaseState.CRIMINAL_APPEAL_FILED},
    CaseState.CRIMINAL_APPEAL_FILED: {CaseState.WAITING_FOR_CRIMINAL_SECOND_TRIAL},
    # ... 二审庭审与终审 ...
    CaseState.CRIMINAL_FINAL_VERDICT: {CaseState.CLOSED},
}
```

有两个值得注意的设计决策：

1. **PROSECUTION_REVIEW 可以跳过 INDICTMENT_FILED 直接到 DEFENSE_OPINION_DRAFTING。** 这反映了实务中的情况：律师不一定等到起诉书正式递交法院后才开始准备辩护词，在审查起诉阶段阅卷后就可以着手起草。

2. **刑事的上诉决策（CRIMINAL_APPEAL_DECISION）和民事的（APPEAL_DECISION）是两条独立的路径。** 它们的状态标签不同、事件触发不同，不会互相干扰。这避免了在一个大类里用 if-else 区分民刑事——状态本身就是最好的区分。

### 5.3 EventBus 事件驱动

EventBus 的 `_RUNTIME_ISSUE_STAGE_MAP` 把事件类型映射到阶段码和阶段中文名。当刑事事件（如 `INVESTIGATION_STARTED`）被发布时，EventBus 能自动解析出当前处于 INV（侦查阶段），从而触发对应的 Scenario。

```python
# event_bus.py 中新增的映射
_RUNTIME_ISSUE_STAGE_MAP = {
    # ... 民事事件 ...
    str(EventType.INVESTIGATION_STARTED): ("INV", "侦查阶段"),
    str(EventType.PROSECUTION_REVIEW_STARTED): ("PR", "审查起诉阶段"),
    str(EventType.ENTER_DEFENSE_OPINION_DRAFTING): ("DS", "辩护词起草"),
    str(EventType.ENTER_CRIMINAL_TRIAL): ("CR", "刑事一审庭审"),
    str(EventType.ENTER_CRIMINAL_APPEAL_TRIAL): ("CRA", "刑事二审庭审"),
}
```

---

## 六、Skill 指令库：LLM 的"操作手册"

### 6.1 Skill 是什么

Skill 不是代码，而是给 LLM 看的 Markdown 指令文件。每个起草阶段有一个对应的 `SKILL.md`，存放在 `legal-skillhub/` 下。LLM Agent 在进入该阶段时加载对应 SKILL.md 作为系统提示的一部分。

### 6.2 辩护词 SKILL.md 的核心设计

`lawyer-defense-opinion-drafting/SKILL.md` 的结构：

```markdown
---
name: lawyer-defense-opinion-drafting
description: 仅在辩护词起草（DS）阶段使用
---

# 目标
帮助被告人起草《辩护词》。先完成正文，再调用工具导出 PDF。

# 对话规则
1. 首个回合先确认被告人是否认罪认罚
2. 每次只问一个关键问题
3. 先确认对起诉书指控的意见，再追问有利证据和量刑情节
4. 不要编造事实、证据、金额、日期、案号或身份信息
5. 同一材料、证据清单或指控项最多追问 1 次

# 文书模板
（包含完整的辩护词四段论结构）

# 调用与收尾
1. 正文写完后立即调用 draft_defense_opinion_document
2. 最终回复末尾紧跟【起草结束】
3. 引用的法条应是《刑法》《刑事诉讼法》
```

这里有几个精心设计的约束：

- **"每次只问一个关键问题"** — 防止 LLM 一次性抛出所有问题，导致对话不自然
- **"同一材料最多追问 1 次"** — 防止 LLM 陷入"请提供更多材料"的死循环
- **"不要编造"规则** — 限制 LLM 在缺乏信息时胡编法条、金额或身份信息
- **四段论辩护结构（事实 → 法律适用 → 量刑 → 证据）** — 引导 LLM 产出符合实务规范的辩护词

这些约束经过了原始项目中民事 SKILL.md 的实践验证——"每轮一问"和"不重复追问"是防止对话无限延长的关键措施。

---

## 七、Player Lawyer 模式：从写死 "plaintiff" 到动态角色

### 7.1 原始实现的局限

原始 `player_lawyer/agent.py` 中的 `PlayerPlaintiffLawyerAgent` 类硬编码了 `role="plaintiff_lawyer"`。这意味着人类玩家只能扮演原告律师。在刑事场景中，玩家需要扮演**被告的辩护律师**。

### 7.2 泛化改造

核心改动只有一处：`create_request()` 中的 role 参数从硬编码改为由 `party_role` 动态决定。

```python
# 原始代码（硬编码）
class PlayerPlaintiffLawyerAgent:
    def step(self, instruction, ...):
        role_label = "plaintiff_lawyer"  # 写死了

# 改造后（动态角色）
class PlayerLawyerAgent:
    def __init__(self, ..., party_role: str = "plaintiff"):
        self._party_role = party_role.lower()

    def step(self, instruction, ...):
        role_label = f"{self._party_role}_lawyer"
        # party_role="plaintiff" → "plaintiff_lawyer"
        # party_role="defendant" → "defendant_lawyer"
```

同时新增了环境变量解析：

```python
def resolve_player_party_role() -> Optional[str]:
    value = os.environ.get("SIMLAW_PLAYER_LAWYER_MODE", "").strip().lower()
    if value in {"plaintiff", "defendant"}:
        return value
    return None

def is_player_defendant_mode() -> bool:
    return resolve_player_party_role() == "defendant"
```

启动时设置 `SIMLAW_PLAYER_LAWYER_MODE=defendant`，玩家就可以扮演刑事辩护律师，通过 WebSocket 网关与 AI 检察官和 AI 法官交互。

---

## 八、刑事 Agent 与场景的定位

### 8.1 检察官 Agent（ProsecutorAgent）

与 LawyerAgent 最关键的区别是 `agent_type`：

```python
class ProsecutorAgent:
    agent_type = "prosecutor"  # 全新的 agent type
```

这个类型字符串被 `stage_tool_resolver.py` 的 `resolve_agent_type()` 识别，从而在 manifest 中找到对应的工具分配。检察官的 system_prompt 围绕公诉立场构建——"代表国家出庭支持公诉"——与律师的中立代理立场截然不同。

### 8.2 侦查员 Agent（InvestigatorAgent）

侦查员 Agent 被标记为"可选"，因为它大多是被动角色（告知罪名、安排会见），实践中可以由数据集驱动而非 AI 生成。它的 `agent_type = "client"`（复用现有类型），避免在系统中引入过多新类型。

### 8.3 两个刑事场景

- **InvestigationScenario（侦查阶段）：** 仿 `legal_consultation.py` 结构。律师与嫌疑人（/侦查员）的对话场景。核心产出是了解涉嫌罪名、收集有利证据线索。
- **ProsecutionReviewScenario（审查起诉阶段）：** 仿 `defense_drafting.py` 结构。律师阅卷后向检察官提交辩护意见。核心产出是 defense_strategy（辩护策略），为后续辩护词起草提供基础。

两个场景都遵循 `BaseScenario` 的协议（`run()` 返回 `stage_output`），可以与原始 Pipeline 无缝衔接。

---

## 九、验证方式

### 语法层（无需依赖）
```bash
cd backend
python -m py_compile src/tools/legal/*.py src/agents/*.py src/scenarios/*.py
```

### 配置层（只需 PyYAML）
```bash
python -c "
import yaml
with open('src/pipeline/stage_tool_manifest.yaml') as f:
    m = yaml.safe_load(f)
print('Stages:', list(m['stages'].keys()))
print('Agent types:', list(m['agent_type_defaults'].keys()))
"
```

### 完整集成（需 camel-ai + reportlab）
```bash
python scripts/verify_criminal.py
```

该脚本逐项检查：模块导入 → 常量注册 → 工具注册表 → Manifest 校验 → 阶段矩阵 → EventBus 事件 → CaseFSM 状态机 → Player Lawyer 模式。
