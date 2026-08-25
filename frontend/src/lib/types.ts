// WebSocket protocol message types — mirrors backend/src/simulation/ws_protocol.py

export type WSMessageType =
  | "agent_spawn"
  | "agent_move"
  | "agent_sit"
  | "agent_stand"
  | "agent_bubble"
  | "agent_despawn"
  | "dialogue_update"
  | "dialogue_gate_waiting"
  | "dialogue_gate_accepted"
  | "dialogue_gate_error"
  | "runtime_progress"
  | "step_gate_waiting"
  | "step_gate_accepted"
  | "step_gate_error"
  | "case_state_change"
  | "scenario_start"
  | "scenario_end"
  | "case_runtime_issue"
  | "agent_goto_front_desk"
  | "agent_update_dialogue"
  | "agent_end_interaction"
  | "client_logout_ack"
  | "player_lawyer_error"
  | "player_lawyer_input_submitted"
  | string;

export interface WSMessage {
  type: WSMessageType;
  [key: string]: unknown;
}

export interface AgentSpawnPayload {
  agent_id: string;
  name: string;
  character_name?: string;
  x?: number;
  y?: number;
  role?: string;
}

export interface DialogueUpdatePayload {
  case_id: string;
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
}

export interface CaseStateChangePayload {
  case_id: string;
  event: string;
  from_state?: string;
  to_state?: string;
  party_role?: string;
  overall_state?: string;
}

export interface ScenarioStartPayload {
  case_id: string;
  scenario_type: string;
  participants: string[];
}

export interface RuntimeProgressPayload {
  case_id: string;
  phase: string;
  message: string;
  detail?: string;
  blocking?: boolean;
  occurred_at?: string;
}

export interface CaseRuntimeIssuePayload {
  case_id: string;
  scenario_type: string;
  stage_label: string;
  code: string;
  message: string;
  retryable: boolean;
  occurred_at: string;
}

// REST types

export interface AuthResponse {
  access_token: string;
  token_type?: string;
  user?: {
    id: string | number;
    email: string;
  };
}

export interface SandboxState {
  sandbox?: {
    id?: string;
    status?: string;
    selected_case_id?: string | null;
  } | null;
  runtime_status?: {
    status?: string;
    running?: boolean;
  };
}

export interface CasePickerEntry {
  case_id: string;
  title: string;
  plaintiff_name: string;
  defendant_name: string;
  case_category?: "criminal";
  raw_case_cause: string;
  training_category?: string;
  difficulty?: string;
  status?: string;
}

export interface CaseListResponse {
  cases: CasePickerEntry[];
  selected_case_id?: string | null;
}

export type PlayerMode = "auto" | "player";

export interface PlayerRequest {
  request_id: string;
  sandbox_id?: string | number;
  case_id?: string;
  stage?: string;
  role?: string;
  speaker_label?: string;
  prompt?: string;
  context_summary?: string;
  status?: string;
  created_at?: string;
  expires_at?: string | null;
}

export interface PlayerAssist {
  request_id: string;
  case_id?: string;
  stage?: string;
  user_original_message?: string;
  ai_polished_message?: string;
  final_submitted_message?: string;
  used_ai_polish?: boolean;
  hint_ids?: string[];
}

export interface PlayerAssistResponse {
  success: boolean;
  assist?: PlayerAssist;
}

export interface StatusResponse {
  status: string;
  backend_version?: string;
  backend_version_label?: string;
  backend_version_time?: string;
  clients_connected?: number;
  simulation_running?: boolean;
}

// ── Teaching subsystem ──────────────────────────────────────────────

export interface CitationFeedbackDetail {
  citation?: string;
  status?: string;
  message?: string;
  content?: string;
  suggestion?: string;
  [key: string]: unknown;
}

export interface CitationFeedback {
  status: string;
  messages: string[];
  details?: CitationFeedbackDetail[] | null;
}

/** 即时法条校验：从 submit 响应透传（PlayerInputPanel → DialogueFeed） */
export interface CitationNotice {
  id: string;
  status?: string;
  messages: string[];
  details: CitationFeedbackDetail[];
}

export interface CapabilityScore {
  score: number;
  raw?: number;
  weight?: number;
  rationale?: string;
  evidence_quote?: string;
}

export interface SubsumptionRow {
  element: string;
  fact_found: string;
  conclusion: string;
  comment?: string;
}

export interface KnowledgeVerdict {
  kp: string;
  status: string;
  reason?: string;
}

export interface LawCitation {
  citation: string;
  status: string;
  content?: string;
  issue?: string;
}

export interface LearningEvent {
  event_id: string;
  schema_version?: string;
  student_id: string;
  case_id: string;
  charge?: string;
  stage: string;
  gold_incomplete?: boolean;
  capability_scores: Record<string, CapabilityScore>;
  subsumption_table?: SubsumptionRow[];
  knowledge_verdicts?: KnowledgeVerdict[];
  error_tags?: string[];
  law_citations?: LawCitation[];
  knowledge_gaps?: string[];
  overall_feedback?: string;
  scored_at?: string;
}

export interface GrowthPoint {
  at: string;
  stage: string;
  case_id: string;
  mean: number;
}

export interface KnowledgeGapEntry {
  kp: string;
  exposed: number;
  latest: string;
}

export interface RecommendationItem {
  chapter?: string;
  question_no?: number | string;
  question: string;
  knowledge_points: string[];
  question_type?: string;
  source?: string;
}

export interface TeachingReport {
  student_id: string;
  capability_radar: Array<{ code: string; name: string; score: number }>;
  knowledge_gaps: KnowledgeGapEntry[];
  top_errors: Array<{ tag: string; count: number }>;
  growth_curve: GrowthPoint[];
  cases_played: string[];
  recommendations: RecommendationItem[];
  updated_at?: string;
}
