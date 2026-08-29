// Case lifecycle metadata — mirrors backend/src/orchestration/case_fsm.py CaseState (纯刑事)

export interface StageInfo {
  /** Backend CaseState value (Chinese label) */
  state: string;
  /** Short stage code shown in the rail */
  code: string;
  /** English/Chinese human-readable label */
  label: string;
  /** Optional scenario_type code from the backend */
  scenarioType?: string;
  /** 特殊节点标记：exit = 提前终止（撤案/不起诉/判决生效） */
  stageKind?: "exit";
}

/** 刑事流程阶段轨 — mirrors backend criminal CaseState chain */
export const CRIMINAL_STAGES: StageInfo[] = [
  { state: "空闲", code: "00", label: "Idle" },
  { state: "等待前台接待", code: "01", label: "Reception" },
  { state: "委托洽谈中", code: "02", label: "Retainer Consult", scenarioType: "LC" },
  { state: "侦查阶段", code: "03", label: "Investigation", scenarioType: "INV" },
  { state: "审查起诉阶段", code: "04", label: "Prosecution Review", scenarioType: "PR" },
  { stageKind: "exit", state: "已结案", code: "040", label: "非罪化终止（撤案/不起诉）" },
  { state: "起诉书已递交", code: "05", label: "Indictment Filed" },
  { state: "辩护词起草中", code: "06", label: "Defense Opinion", scenarioType: "DS" },
  { state: "辩护词已递交", code: "07", label: "Defense Filed" },
  { state: "等待刑事一审开庭", code: "08", label: "Awaiting First Trial" },
  { state: "刑事一审庭审中", code: "09", label: "Criminal First Trial", scenarioType: "CR" },
  { state: "刑事一审判决", code: "10", label: "First Verdict" },
  { state: "刑事上诉决策中", code: "11", label: "Appeal / Protest Decision" },
  { state: "刑事上诉状起草中", code: "12", label: "Appeal / Protest Draft" },
  { state: "刑事上诉状已递交", code: "13", label: "Appeal / Protest Filed" },
  { state: "等待刑事二审开庭", code: "14", label: "Awaiting Second Trial" },
  { state: "刑事二审庭审中", code: "15", label: "Criminal Appeal Trial", scenarioType: "CRA" },
  { state: "刑事终审判决", code: "16", label: "Final Verdict" },
  { state: "已结案", code: "17", label: "Closed" },
];

export type CaseCategory = "criminal";

export function displayState(state: string | undefined | null, _category?: CaseCategory | null): string {
  return state ?? "";
}

const CRIMINAL_STAGE_INDEX = new Map(CRIMINAL_STAGES.map((s, i) => [s.state, i]));

/** 已走过的场景阶段 code（用于区分「顺序经过」与「跳过」） */
const VISITED_SCENARIO_CODES = new Set<string>();

/** 记录一次经过的场景阶段（scenarioType 唯一，无歧义） */
export function markStageVisited(scenarioType: string | undefined | null) {
  const code = (scenarioType ?? "").trim().toUpperCase();
  if (!code) return;
  VISITED_SCENARIO_CODES.add(code);
}

/** 该场景阶段是否真实经过（提前终止时未被经过的节点保持灰色） */
export function isStageVisited(scenarioType: string | undefined | null): boolean {
  const code = (scenarioType ?? "").trim().toUpperCase();
  return !!code && VISITED_SCENARIO_CODES.has(code);
}

export function resetStageVisits() {
  VISITED_SCENARIO_CODES.clear();
}

/** 状态字符串 → scenarioType code（用于标记「真实经过」） */
const STATE_TO_SCENARIO = new Map(
  CRIMINAL_STAGES
    .filter((s) => s.scenarioType)
    .map((s) => [s.state, s.scenarioType as string]),
);

export function scenarioTypeForState(state: string | undefined | null): string {
  return STATE_TO_SCENARIO.get(String(state ?? "")) ?? "";
}

/**
 * 「已结案」歧义消解：exit 节点（040）与终点（17）共用同一状态字符串。
 * 只有真实顺序经过起诉及以后阶段时才指向终点 17，否则落在 exit 节点 040。
 */
const TERMINAL_CODE = "17";
const EXIT_CODE = "040";
const POST_EXIT_CODES = new Set(["05", "06", "07", "08", "09", "10", "11", "12", "13", "14", "15", "16"]);

export function stageIndexOf(
  state: string | undefined | null,
  _category: CaseCategory | null | undefined = "criminal",
): number {
  if (!state) return -1;
  const indices: number[] = [];
  CRIMINAL_STAGES.forEach((s, i) => {
    if (s.state === state) indices.push(i);
  });
  if (indices.length <= 1) {
    return indices[0] ?? CRIMINAL_STAGE_INDEX.get(state) ?? -1;
  }
  // 「已结案」双节点：出现过任一起诉后阶段 → 终点；否则 → 提前终止分支
  if (state === "已结案") {
    const reachedEnd = CRIMINAL_STAGES.some(
      (s) => POST_EXIT_CODES.has(s.code) && VISITED_SCENARIO_CODES.has((s.scenarioType ?? "").toUpperCase()),
    );
    return reachedEnd
      ? CRIMINAL_STAGES.findIndex((s) => s.code === TERMINAL_CODE)
      : CRIMINAL_STAGES.findIndex((s) => s.code === EXIT_CODE);
  }
  return indices[0];
}

export function isTerminal(state: string | undefined | null): boolean {
  return state === "已结案";
}

export function scenarioLabel(scenarioType: string | undefined | null): string {
  switch ((scenarioType ?? "").toUpperCase()) {
    case "LC":
      return "委托洽谈";
    case "INV":
      return "侦查阶段";
    case "PR":
      return "审查起诉";
    case "DS":
      return "辩护词起草";
    case "CR":
      return "刑事一审庭审";
    case "CRA":
      return "刑事二审庭审";
    default:
      return scenarioType ?? "";
  }
}

/** Color cue for a stage — vermillion intensity grows toward verdict */
export function stageAccent(state: string | undefined | null): string {
  if (!state) return "var(--accent-muted)";
  if (state === "已结案") return "var(--accent-success)";
  if (state.includes("判决")) return "var(--accent)";
  if (state?.includes("庭审")) return "var(--accent)";
  if (state?.includes("委托洽谈") || state?.includes("咨询")) return "var(--accent-amber)";
  return "var(--accent-muted)";
}
