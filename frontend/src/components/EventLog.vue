<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useSession } from "../composables/useSession";

const session = useSession();
const logEl = ref<HTMLElement | null>(null);

const entries = computed(() => session.events.value);

watch(
  () => entries.value.length,
  async () => {
    await nextTick();
    const el = logEl.value;
    if (el) el.scrollTop = el.scrollHeight;
  },
);

function stamp(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString("en-GB", { hour12: false });
}

function toneFor(type: string): string {
  if (type === "case_state_change") return "tone--accent";
  if (type === "scenario_start") return "tone--amber";
  if (type === "case_runtime_issue") return "tone--err";
  if (type === "scenario_end") return "tone--success";
  return "";
}

/** 事件类型 → 中文短标签 */
function typeLabel(type: string): string {
  const map: Record<string, string> = {
    agent_spawn: "入场",
    agent_despawn: "退场",
    agent_bubble: "发言",
    agent_update_dialogue: "发言",
    agent_goto_front_desk: "前台",
    dialogue_update: "对话",
    case_state_change: "阶段",
    scenario_start: "场景",
    scenario_end: "完成",
    runtime_progress: "进度",
    case_runtime_issue: "异常",
    dialogue_gate_waiting: "等待",
    step_gate_waiting: "等待",
    dialogue_gate_accepted: "继续",
  };
  return map[type] ?? type;
}

const TOOL_DETAIL_TYPES = new Set([
  "runtime_progress",
  "case_runtime_issue",
  "scenario_start",
]);

/** detail 只保留工具调用/系统过程类，发言原文不再重复展示 */
function showDetail(type: string, detail?: string): boolean {
  if (!detail) return false;
  return TOOL_DETAIL_TYPES.has(type);
}
</script>

<template>
  <section class="log">
    <header class="log__head">
      <p class="kicker">RUNTIME EVENTS</p>
      <h3>事件日志</h3>
      <span class="tag log__count">{{ entries.length }}</span>
    </header>

    <div class="log__body" ref="logEl">
      <div v-if="entries.length === 0" class="log__empty muted">
        — 等待事件 —
      </div>

      <div
        v-for="entry in entries"
        :key="entry.id"
        class="evt"
        :class="toneFor(entry.type)"
      >
        <span class="evt__time mono">{{ stamp(entry.occurred_at) }}</span>
        <span class="evt__type mono">{{ typeLabel(entry.type) }}</span>
        <span class="evt__summary">{{ entry.summary }}</span>
        <span
          v-if="showDetail(entry.type, entry.detail)"
          class="evt__detail dim mono"
          :title="entry.detail"
        >
          {{ entry.detail }}
        </span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.log {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
}

.log__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
}
.kicker {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 0.62rem;
  letter-spacing: 0.2em;
  color: var(--accent);
  flex-basis: 100%;
}
.log__head h3 {
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 1.05rem;
  font-weight: 600;
  margin: 0;
  flex: 1;
}
.log__count { font-size: 0.7rem; }

.log__body {
  font-family: var(--font-mono);
  font-size: 0.74rem;
  line-height: 1.5;
  overflow-y: auto;
  flex: 1;
  padding-right: 4px;
}

.log__empty {
  font-style: italic;
  text-align: center;
  padding: 20px 0;
}

/* 单行横向布局：时间 · 类型 · 摘要 —— 超长截断 */
.evt {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 3px 0;
  border-bottom: 1px dashed var(--line-faint);
  white-space: nowrap;
  overflow: hidden;
}
.evt__time {
  color: var(--parchment-dim);
  flex-shrink: 0;
}
.evt__type {
  color: var(--accent-amber);
  flex-shrink: 0;
}
.evt__summary {
  color: var(--parchment);
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}
.evt__detail {
  color: var(--parchment-dim);
  font-size: 0.68rem;
  overflow: hidden;
  text-overflow: ellipsis;
  flex-shrink: 1;
  max-width: 40%;
}

.evt.tone--accent .evt__type { color: var(--accent); }
.evt.tone--amber .evt__type { color: var(--accent-amber); }
.evt.tone--success .evt__type { color: var(--accent-success); }
.evt.tone--err .evt__type { color: var(--accent); }
.evt.tone--err .evt__summary { color: #f0b6a6; }
</style>
