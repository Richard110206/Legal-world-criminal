import { computed, reactive, readonly } from "vue";
import { api, setToken } from "../lib/api";
import { WSClient, type ClientMode, type WSStatus } from "../lib/ws";
import type {
  CasePickerEntry,
  CitationNotice,
  LearningEvent,
  PlayerMode,
  PlayerRequest,
  WSMessage,
} from "../lib/types";
import type { CaseCategory } from "../lib/caseState";
import { markStageVisited, resetStageVisits, scenarioTypeForState } from "../lib/caseState";
import { agentDisplayName, roleName } from "../lib/roleNames";

export interface AgentRecord {
  agent_id: string;
  name: string;
  character_name?: string;
  role?: string;
  last_bubble?: string;
  spawned_at: number;
  last_active_at: number;
}

export interface DialogueEntry {
  id: string;
  case_id?: string;
  speaker_id: string;
  speaker_name: string;
  content: string;
  turn: number;
  scenario_type?: string;
  generation_duration_seconds?: number;
  generation_total_tokens?: number;
  player_responsibility?: boolean;
  evaluation_marker_label?: string;
  evaluation_marker_reason?: string;
  arrived_at: number;
}

export interface EventLogEntry {
  id: string;
  type: string;
  summary: string;
  detail?: string;
  occurred_at: number;
  /** 原始 WS 消息（工具面板等需要完整字段） */
  raw?: WSMessage;
}

interface GateInfo {
  gateId: string;
  speakerName: string;
  turn: number;
}

interface SessionState {
  initialized: boolean;
  email: string | null;
  wsStatus: WSStatus;
  wsError: string | null;
  caseState: string;
  caseOverallState: string;
  caseId: string | null;
  caseCategory: CaseCategory;
  sandboxStatus: string | null;
  simulationRunning: boolean;
  backendVersion: string | null;
  agents: Record<string, AgentRecord>;
  dialogue: DialogueEntry[];
  events: EventLogEntry[];
  cases: CasePickerEntry[];
  selectedCaseId: string | null;
  playerMode: PlayerMode;
  pendingPlayerRequest: PlayerRequest | null;
  waitingGate: GateInfo | null;
  /** 即时法条校验通知（提交后由 PlayerInputPanel 推入，DialogueFeed 消费） */
  citationNotice: CitationNotice | null;
  /** 已批阅阶段的 LearningEvent，键为 stage 码（LC/INV/PR/DS/CR/CRA） */
  reviewedStages: Record<string, LearningEvent>;
}

const state = reactive<SessionState>({
  initialized: false,
  email: null,
  wsStatus: "idle",
  wsError: null,
  caseState: "",
  caseOverallState: "",
  caseId: null,
  caseCategory: "criminal",
  sandboxStatus: null,
  simulationRunning: false,
  backendVersion: null,
  agents: {},
  dialogue: [],
  events: [],
  cases: [],
  selectedCaseId: null,
  playerMode: "auto",
  pendingPlayerRequest: null,
  waitingGate: null,
  citationNotice: null,
  reviewedStages: {},
});

const DIALOGUE_LIMIT = 200;
const EVENT_LIMIT = 300;

/** 状态转移 → 刚结束的阶段码（与后端 orchestrator 触发评分的阶段对齐） */
const STATE_EXIT_TO_STAGE: Array<{ from: string; stage: string }> = [
  { from: "委托洽谈中", stage: "LC" },
  { from: "侦查阶段", stage: "INV" },
  { from: "审查起诉阶段", stage: "PR" },
  { from: "辩护词起草中", stage: "DS" },
  { from: "刑事一审庭审中", stage: "CR" },
  { from: "刑事二审庭审中", stage: "CRA" },
];

let wsClient: WSClient | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;
let reviewPollers: Array<ReturnType<typeof setTimeout>> = [];
let idCounter = 0;
function nextId(): string {
  idCounter += 1;
  return `${Date.now().toString(36)}-${idCounter.toString(36)}`;
}

function pushDialogue(entry: Omit<DialogueEntry, "id" | "arrived_at">) {
  state.dialogue.push({ ...entry, id: nextId(), arrived_at: Date.now() });
  if (state.dialogue.length > DIALOGUE_LIMIT) {
    state.dialogue.splice(0, state.dialogue.length - DIALOGUE_LIMIT);
  }
}

function pushEvent(type: string, summary: string, detail?: string, raw?: WSMessage) {
  state.events.push({
    id: nextId(),
    type,
    summary,
    detail,
    occurred_at: Date.now(),
    raw,
  });
  if (state.events.length > EVENT_LIMIT) {
    state.events.splice(0, state.events.length - EVENT_LIMIT);
  }
}

function handleSpawn(msg: WSMessage) {
  const agent_id = String(msg.agent_id ?? "");
  if (!agent_id) return;
  const name = String(msg.name ?? agent_id);
  const character_name = msg.character_name ? String(msg.character_name) : undefined;
  const role = msg.role ? String(msg.role) : undefined;
  state.agents[agent_id] = {
    agent_id,
    name,
    character_name,
    role,
    spawned_at: Date.now(),
    last_active_at: Date.now(),
  };
  pushEvent(
    "agent_spawn",
    `${agentDisplayName(name, role, character_name)} 入场`,
    role ? `身份: ${roleName(role)}` : undefined,
  );
}

function handleBubble(msg: WSMessage) {
  const agent_id = String(msg.agent_id ?? "");
  const agent = state.agents[agent_id];
  const text = String(msg.text ?? "");
  if (agent) {
    agent.last_bubble = text;
    agent.last_active_at = Date.now();
  }
  const displayName = agentDisplayName(
    agent?.name ?? agent_id,
    agent?.role,
    agent?.character_name,
  );
  pushEvent("agent_bubble", `${displayName}: ${text}`);
}

function handleDespawn(msg: WSMessage) {
  const agent_id = String(msg.agent_id ?? "");
  const agent = state.agents[agent_id];
  if (agent) {
    delete state.agents[agent_id];
    pushEvent(
      "agent_despawn",
      `${agentDisplayName(agent.name, agent.role, agent.character_name)} 退场`,
    );
  }
}

/** speaker_name 可能是后端系统名（case_1_plaintiff/Samuel），经 agents 注册表转中文 */
function resolveSpeakerName(speakerId: string, rawName: string): string {
  const agent = speakerId ? state.agents[speakerId] : undefined;
  const resolved = agentDisplayName(
    rawName || agent?.name || speakerId,
    agent?.role,
    agent?.character_name,
  );
  return resolved || "未知";
}

function handleDialogue(msg: WSMessage) {
  pushDialogue({
    case_id: msg.case_id ? String(msg.case_id) : undefined,
    speaker_id: String(msg.speaker_id ?? ""),
    speaker_name: resolveSpeakerName(String(msg.speaker_id ?? ""), String(msg.speaker_name ?? "")),
    content: String(msg.content ?? ""),
    turn: Number(msg.turn ?? 0),
    scenario_type: msg.scenario_type ? String(msg.scenario_type) : undefined,
    generation_duration_seconds:
      typeof msg.generation_duration_seconds === "number"
        ? msg.generation_duration_seconds
        : undefined,
    generation_total_tokens:
      typeof msg.generation_total_tokens === "number"
        ? msg.generation_total_tokens
        : undefined,
    player_responsibility: msg.player_responsibility === true,
    evaluation_marker_label: msg.evaluation_marker_label
      ? String(msg.evaluation_marker_label)
      : undefined,
    evaluation_marker_reason: msg.evaluation_marker_reason
      ? String(msg.evaluation_marker_reason)
      : undefined,
  });
}

function handleCaseStateChange(msg: WSMessage) {
  const toState = typeof msg.to_state === "string" ? msg.to_state : null;
  if (toState === "空闲") resetStageVisits();
  if (toState) state.caseState = toState;
  if (typeof msg.overall_state === "string")
    state.caseOverallState = msg.overall_state;
  if (typeof msg.case_id === "string") state.caseId = msg.case_id;
  const fromState = String(msg.from_state ?? "");
  // 刑事案共用民事入口（接待/咨询），此后进入刑事专属状态链
  if (
    fromState &&
    !fromState.includes("刑事") &&
    toState &&
    (toState.includes("刑事") || toState === "侦查阶段" || toState === "审查起诉阶段")
  ) {
    state.caseCategory = "criminal";
  }
  // 离开的状态即视为「真实经过」——提前终止时未经过的节点保持灰色
  markStageVisited(scenarioTypeForState(fromState));
  markStageVisited(scenarioTypeForState(toState));
  pushEvent(
    "case_state_change",
    `阶段转移: ${msg.from_state ?? "—"} → ${msg.to_state ?? "—"}`,
    msg.event ? `事件: ${msg.event}` : undefined,
  );
  scheduleReviewPolling(fromState);
}

function handleScenarioStart(msg: WSMessage) {
  const participants = Array.isArray(msg.participants)
    ? msg.participants.join(", ")
    : "";
  markStageVisited(typeof msg.scenario_type === "string" ? msg.scenario_type : "");
  pushEvent(
    "scenario_start",
    `场景启动: ${msg.scenario_type ?? ""}`,
    participants ? `参与人: ${participants}` : undefined,
  );
}

function handleScenarioEnd(msg: WSMessage) {
  pushEvent(
    "scenario_end",
    `场景结束: ${msg.scenario_type ?? ""}`,
  );
}

function handleRuntimeProgress(msg: WSMessage) {
  pushEvent(
    "runtime_progress",
    String(msg.message ?? ""),
    typeof msg.detail === "string" ? msg.detail : undefined,
    msg,
  );
}

function handleCaseRuntimeIssue(msg: WSMessage) {
  pushEvent(
    "case_runtime_issue",
    `[${msg.code ?? "ERR"}] ${msg.message ?? ""}`,
    msg.stage_label ? `阶段: ${msg.stage_label}` : undefined,
  );
}

function handleGateWaiting(msg: WSMessage) {
  const gateId = String(msg.gate_id ?? "");
  if (gateId) {
    state.waitingGate = {
      gateId,
      speakerName: resolveSpeakerName(String(msg.agent_id ?? ""), String(msg.speaker_name ?? "")),
      turn: Number(msg.turn ?? 0),
    };
  }
  pushEvent(
    String(msg.type ?? ""),
    `等待继续: ${msg.speaker_name ?? "发言"}`,
    `回合 ${msg.turn ?? "?"}`,
  );
}

function handleGateResolved(msg: WSMessage) {
  const gateId = String(msg.gate_id ?? "");
  if (!gateId || !state.waitingGate) return;
  if (state.waitingGate.gateId === gateId) {
    state.waitingGate = null;
  }
}

/** PlayerInputPanel 提交成功后推入即时法条校验警示 */
function pushCitationNotice(notice: CitationNotice | null) {
  state.citationNotice = notice;
}

function dismissCitationNotice() {
  state.citationNotice = null;
}

function markStageReviewed(stage: string, event: LearningEvent) {
  state.reviewedStages[stage.toUpperCase()] = event;
}

/**
 * 阶段结束（状态从 X 转走）后轮询批阅结果。
 * 后端评分是异步线程（LLM 裁判 ~30-90s），无 ws 推送，所以轮询：
 * 20s 起查，之后每 20s 一次，最多 12 次（4 分钟），拿到即停。
 */
function scheduleReviewPolling(fromState: string) {
  if (state.playerMode !== "player") return;
  const hit = STATE_EXIT_TO_STAGE.find((m) => m.from === fromState);
  if (!hit) return;
  const stage = hit.stage;
  if (state.reviewedStages[stage]) return;
  const caseId = state.caseId;
  if (!caseId) return;

  let attempts = 0;
  const tick = () => {
    attempts += 1;
    void (async () => {
      try {
        const event = await api.teachingEvent(caseId, stage);
        if (event) {
          markStageReviewed(stage, event);
          pushEvent(
            "teaching_score_ready",
            `阶段批阅完成: ${stage} · 均分 ${meanScore(event)}`,
          );
          return;
        }
      } catch {
        /* backend busy — keep polling until attempts exhausted */
      }
      if (attempts < 12) {
        reviewPollers.push(setTimeout(tick, 20_000));
      }
    })();
  };
  reviewPollers.push(setTimeout(tick, 20_000));
}

function meanScore(event: LearningEvent): string {
  const scores = Object.values(event.capability_scores ?? {});
  if (!scores.length) return "—";
  const mean = scores.reduce((acc, s) => acc + (Number(s.score) || 0), 0) / scores.length;
  return (mean * 10).toFixed(1);
}

function clearReviewPollers() {
  reviewPollers.forEach(clearTimeout);
  reviewPollers = [];
}

function dispatchMessage(msg: WSMessage) {
  switch (msg.type) {
    case "agent_spawn":
      return handleSpawn(msg);
    case "agent_bubble":
      return handleBubble(msg);
    case "agent_despawn":
      return handleDespawn(msg);
    case "agent_update_dialogue":
    case "agent_goto_front_desk": {
      const agent_id = String(msg.agent_id ?? "");
      const agent = state.agents[agent_id];
      const text =
        typeof msg.dialogue_text === "string" ? msg.dialogue_text : "";
      if (agent && text) {
        agent.last_bubble = text;
        agent.last_active_at = Date.now();
      }
      if (text) pushEvent(String(msg.type), text);
      return;
    }
    case "dialogue_update":
      return handleDialogue(msg);
    case "case_state_change":
      return handleCaseStateChange(msg);
    case "scenario_start":
      return handleScenarioStart(msg);
    case "scenario_end":
      return handleScenarioEnd(msg);
    case "runtime_progress":
      return handleRuntimeProgress(msg);
    case "case_runtime_issue":
      return handleCaseRuntimeIssue(msg);
    case "dialogue_gate_waiting":
    case "step_gate_waiting":
      return handleGateWaiting(msg);
    case "dialogue_gate_accepted":
    case "step_gate_accepted":
    case "dialogue_gate_error":
    case "step_gate_error":
      return handleGateResolved(msg);
    default:
      pushEvent(String(msg.type ?? "unknown"), JSON.stringify(msg).slice(0, 200));
  }
}

export function useSession() {
  async function init() {
    if (state.initialized) return;
    state.initialized = true;
    try {
      const status = await api.status();
      state.backendVersion = status.backend_version_label ?? null;
    } catch (err) {
      state.wsError = err instanceof Error ? err.message : String(err);
    }
  }

  async function login(email: string, password: string) {
    const res = await api.login(email, password);
    setToken(res.access_token);
    state.email = email;
    await postAuth();
  }

  async function register(email: string, password: string) {
    const res = await api.register(email, password);
    setToken(res.access_token);
    state.email = email;
    await postAuth();
  }

  async function postAuth() {
    await api.ensureSandbox();
    await refreshSandbox();
    await refreshCases();
    connectWS();
  }

  async function refreshSandbox() {
    try {
      const sb = await api.getSandbox();
      state.sandboxStatus = sb.sandbox?.status ?? sb.runtime_status?.status ?? null;
      state.simulationRunning = sb.runtime_status?.running ?? false;
      // 页面刷新后 playerMode 会回退成 "auto"，从后端恢复，否则输入面板永不弹出
      if (state.playerMode !== "player") {
        try {
          const rt = await api.playerRuntime();
          if (rt.enabled && rt.player_mode === "defendant") {
            state.playerMode = "player";
            startPollingIfPlayer();
          }
        } catch {
          /* player-lawyer 未启用时静默 */
        }
      }
    } catch (err) {
      state.wsError = err instanceof Error ? err.message : String(err);
    }
  }

  async function refreshCases() {
    try {
      const res = await api.listCases();
      state.cases = res.cases ?? [];
      if (res.selected_case_id) {
        state.selectedCaseId = res.selected_case_id;
      } else if (!state.selectedCaseId && state.cases.length > 0) {
        state.selectedCaseId = state.cases[0].case_id;
      }
      syncCaseCategory();
    } catch (err) {
      state.wsError = err instanceof Error ? err.message : String(err);
    }
  }

  function syncCaseCategory() {
    const selected = state.cases.find((c) => c.case_id === state.selectedCaseId);
    if (selected?.case_category === "criminal") {
      state.caseCategory = "criminal";
    } else {
      state.caseCategory = "criminal";
    }
  }

  function selectCase(caseId: string) {
    state.selectedCaseId = caseId;
    syncCaseCategory();
  }

  async function startSimulation(caseId?: string) {
    const id = caseId ?? state.selectedCaseId ?? state.cases[0]?.case_id;
    if (!id) {
      state.wsError = "没有可用案件";
      return;
    }
    try {
      await api.startSandbox(id);
      state.selectedCaseId = id;
      await refreshSandbox();
      startPollingIfPlayer();
    } catch (err) {
      state.wsError = err instanceof Error ? err.message : String(err);
    }
  }

  async function pauseSimulation() {
    try {
      await api.pauseSandbox();
      await refreshSandbox();
    } catch (err) {
      state.wsError = err instanceof Error ? err.message : String(err);
    }
  }

  async function restartSimulation() {
    try {
      await api.restartSandbox();
      await refreshSandbox();
    } catch (err) {
      state.wsError = err instanceof Error ? err.message : String(err);
    }
  }

  function dismissError() {
    state.wsError = null;
  }

  function connectWS() {
    const token = localStorage.getItem("lw.token");
    if (!token) return;
    wsClient?.close();
    wsClient = new WSClient({
      token,
      mode: state.playerMode === "player" ? "player" : "auto",
      onStatus: (s) => {
        state.wsStatus = s;
        if (s === "unauthorized") {
          state.wsError = "WebSocket 鉴权失败,请重新登录";
          logout();
        } else if (s === "open") {
          state.wsError = null;
        }
      },
    });
    wsClient.on(dispatchMessage);
    wsClient.connect();
    startPollingIfPlayer();
  }

  function setPlayerMode(mode: PlayerMode) {
    state.playerMode = mode;
    if (wsClient) {
      const clientMode: ClientMode = mode === "player" ? "player" : "auto";
      wsClient.setMode(clientMode);
    }
    startPollingIfPlayer();
  }

  function startPollingIfPlayer() {
    stopPolling();
    if (state.playerMode !== "player") {
      state.pendingPlayerRequest = null;
      return;
    }
    pollTimer = setInterval(refreshPlayerRuntime, 2500);
    void refreshPlayerRuntime();
  }

  function stopPolling() {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function refreshPlayerRuntime() {
    if (state.playerMode !== "player") return;
    try {
      const res = await api.playerRuntime(state.caseId ?? undefined);
      const pending = res.pending?.[0];
      state.pendingPlayerRequest = pending ?? null;
    } catch (err) {
      // silent — polling errors shouldn't spam the UI
      if (!(err instanceof Error) || !err.message.includes("404")) {
        // eslint-disable-next-line no-console
        console.warn("[player-runtime]", err);
      }
    }
  }

  function clearPendingPlayerRequest() {
    state.pendingPlayerRequest = null;
  }

  function sendDialogueContinue(gateId: string) {
    wsClient?.send({ type: "dialogue_continue", gate_id: gateId });
  }

  /** 点击页面继续下一段对话（玩家模式 gate 等待时调用） */
  function continueDialogue() {
    const gate = state.waitingGate;
    if (!gate) return false;
    wsClient?.send({ type: "dialogue_continue", gate_id: gate.gateId });
    state.waitingGate = null;
    return true;
  }

  function pauseStream() {
    wsClient?.send({ type: "simulation_pause" });
  }

  function resumeStream() {
    wsClient?.send({ type: "simulation_resume" });
  }

  function logout() {
    stopPolling();
    clearReviewPollers();
    wsClient?.close();
    wsClient = null;
    setToken(null);
    state.email = null;
    state.agents = {};
    state.dialogue = [];
    state.events = [];
    state.caseState = "";
    state.caseOverallState = "";
    state.caseId = null;
    state.caseCategory = "criminal";
    state.pendingPlayerRequest = null;
    state.waitingGate = null;
    state.citationNotice = null;
    state.reviewedStages = {};
  }

  return {
    state: readonly(state),
    backendVersion: computed(() => state.backendVersion),
    agents: computed(() => Object.values(state.agents)),
    dialogue: computed(() => state.dialogue),
    events: computed(() => state.events),
    cases: computed(() => state.cases),
    pendingPlayerRequest: computed(() => state.pendingPlayerRequest),
    init,
    login,
    register,
    logout,
    refreshSandbox,
    refreshCases,
    selectCase,
    setPlayerMode,
    startSimulation,
    pauseSimulation,
    restartSimulation,
    sendDialogueContinue,
    continueDialogue,
    pauseStream,
    resumeStream,
    dismissError,
    clearPendingPlayerRequest,
    refreshPlayerRuntime,
    pushCitationNotice,
    dismissCitationNotice,
    markStageReviewed,
  };
}
