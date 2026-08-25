<script setup lang="ts">
import { computed, ref } from "vue";
import { useSession } from "../composables/useSession";
import CaseModeDialog from "./CaseModeDialog.vue";
import type { CasePickerEntry, PlayerMode } from "../lib/types";

const session = useSession();
const cases = computed(() => session.cases.value);

/** 大类罪名显示清理（数据集存在「…秩序2罪」等脏字符） */
function cleanCause(cause: string | undefined): string {
  return (cause ?? "").replace(/\d+罪/g, "罪").trim();
}

const dialogOpen = ref(false);
const dialogEntry = ref<CasePickerEntry | null>(null);

function openModeDialog(entry: CasePickerEntry) {
  dialogEntry.value = entry;
  dialogOpen.value = true;
}

function closeDialog() {
  dialogOpen.value = false;
  dialogEntry.value = null;
}

async function onConfirm(mode: PlayerMode, caseId: string) {
  closeDialog();
  session.selectCase(caseId);
  session.setPlayerMode(mode);
  await session.startSimulation(caseId);
}
</script>

<template>
  <section class="picker">
    <header class="picker__head">
      <div>
        <p class="kicker">CASE FILES</p>
        <h2>选择案件</h2>
        <p class="picker__sub muted">
          点击案件卡片选择参与模式 —— 自动旁观 AI 对战,或扮演辩护律师亲自上场。
        </p>
      </div>
      <span class="tag">{{ cases.length }} available</span>
    </header>

    <div class="picker__grid">
      <article
        v-for="entry in cases"
        :key="entry.case_id"
        class="case"
        :class="{
          'case--selected': session.state.selectedCaseId === entry.case_id,
        }"
        @click="openModeDialog(entry)"
      >
        <div class="case__tab">
          <span class="case__tabLabel mono">{{ entry.case_id }}</span>
        </div>
        <div class="case__body">
          <p class="case__title">{{ entry.title }}</p>
          <div class="case__meta">
            <span v-if="entry.raw_case_cause" class="tag tag--amber">
              {{ cleanCause(entry.raw_case_cause) }}
            </span>
            <span v-if="entry.training_category" class="tag">
              {{ entry.training_category }}
            </span>
            <span v-if="entry.difficulty" class="tag">
              {{ entry.difficulty }}
            </span>
            <span v-if="entry.status" class="tag tag--success">
              {{ entry.status }}
            </span>
          </div>
        </div>
        <button
          class="case__start"
          :disabled="session.state.simulationRunning"
          @click.stop="openModeDialog(entry)"
        >
          选择 →
        </button>
      </article>
    </div>

    <CaseModeDialog
      :open="dialogOpen"
      :case-entry="dialogEntry"
      @close="closeDialog"
      @confirm="onConfirm"
    />
  </section>
</template>

<style scoped>
.picker {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  padding-right: 6px;
}

.picker__head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 20px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
}

.kicker {
  margin: 0 0 4px;
  font-family: var(--font-mono);
  font-size: 0.68rem;
  letter-spacing: 0.22em;
  color: var(--accent);
}

.picker__head h2 {
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 1.5rem;
  font-weight: 700;
  margin: 0;
}

.picker__sub {
  margin: 6px 0 0;
  font-size: 0.84rem;
  font-style: italic;
}

.picker__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
}

.case {
  position: relative;
  display: grid;
  grid-template-columns: 38px 1fr auto;
  gap: 14px;
  padding: 14px 16px 14px 0;
  background: linear-gradient(180deg, var(--ink-750), var(--ink-800));
  border: 1px solid var(--line);
  border-radius: var(--radius);
  cursor: pointer;
  transition: all 0.18s ease;
  overflow: hidden;
  animation: ink-rise 0.4s ease both;
}
.case::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: transparent;
  transition: background 0.2s ease;
}
.case:hover {
  border-color: var(--line-strong);
  transform: translateY(-2px);
  box-shadow: 0 24px 40px -30px rgba(0, 0, 0, 0.7);
}
.case--selected {
  border-color: rgba(196, 71, 27, 0.6);
  background: linear-gradient(180deg, rgba(196, 71, 27, 0.06), var(--ink-800));
}
.case--selected::before {
  background: var(--accent);
}

.case__tab {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 6px 0 0 12px;
}
.case__tabLabel {
  writing-mode: vertical-rl;
  transform: rotate(180deg);
  font-size: 0.68rem;
  letter-spacing: 0.18em;
  color: var(--accent);
  border-left: 1px solid var(--accent);
  padding-left: 6px;
}

.case__body {
  min-width: 0;
}

.case__title {
  margin: 0 0 4px;
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 1rem;
  font-weight: 600;
  color: var(--parchment);
  line-height: 1.35;
}

.case__cause {
  display: none;
}

.case__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.case__meta .tag {
  font-size: 0.66rem;
  padding: 1px 6px;
}

.case__start {
  align-self: center;
  background: transparent;
  border: 1px solid var(--line-strong);
  color: var(--parchment-muted);
  font-family: var(--font-display);
  font-size: 0.82rem;
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;
}
.case__start:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.case__start:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
