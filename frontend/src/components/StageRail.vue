<script setup lang="ts">
import { computed, ref } from "vue";
import { useSession } from "../composables/useSession";
import { CRIMINAL_STAGES, isStageVisited, stageIndexOf } from "../lib/caseState";
import StageReviewDrawer from "./StageReviewDrawer.vue";

const session = useSession();

const isCriminal = computed(() => session.state.caseCategory === "criminal");
const currentIndex = computed(() =>
  stageIndexOf(session.state.caseState, session.state.caseCategory),
);

const reviewedStages = computed(() => session.state.reviewedStages);

const drawerStage = ref<string | null>(null);
const drawerEvent = computed(() =>
  drawerStage.value ? reviewedStages.value[drawerStage.value] ?? null : null,
);

function openReview(stageCode: string) {
  if (reviewedStages.value[stageCode]) drawerStage.value = stageCode;
}

const grouped = computed(() => {
  const stages = CRIMINAL_STAGES;
  return stages.map((stage, idx) => {
    const active = idx === currentIndex.value;
    // 仅当该阶段被真实经过（或位于当前位置之前的非场景节点）才点亮为「已过」；
    // 提前终止/跳过的中间场景节点保持灰色，直观呈现实际进度
    const visited =
      (stage.scenarioType ? isStageVisited(stage.scenarioType) : idx <= currentIndex.value);
    const passed =
      currentIndex.value >= 0 && idx < currentIndex.value && visited;
    return {
      ...stage,
      display: stage.state,
      active,
      passed,
      skipped:
        currentIndex.value >= 0 &&
        idx < currentIndex.value &&
        stage.scenarioType &&
        !visited,
      upcoming: currentIndex.value >= 0 && idx > currentIndex.value,
      locked: currentIndex.value < 0 && idx > 0,
      reviewed: stage.scenarioType ? !!reviewedStages.value[stage.scenarioType] : false,
    };
  });
});

const currentStage = computed(() =>
  currentIndex.value >= 0 ? CRIMINAL_STAGES[currentIndex.value] : null,
);

const caption = computed(() => "接待 → 侦查 → 审查起诉 → 辩护 → 一审 → 二审");
</script>

<template>
  <nav class="rail">
    <header class="rail__head">
      <p class="rail__kicker">LIFE-CYCLE</p>
      <h2 class="rail__title">阶段轨道</h2>
      <p class="rail__caption muted">
        {{ caption }}
      </p>
      <p class="rail__cat" v-if="isCriminal">刑事流程</p>
    </header>

    <div v-if="currentStage" class="rail__now">
      当前位置 · <strong>{{ currentStage.code }} {{ currentStage.label }}</strong>
      <span class="rail__nowState">{{ currentStage.state }}</span>
    </div>

    <ol class="rail__list">
      <li
        v-for="(stage, idx) in grouped"
        :key="stage.code"
        class="step"
        :class="{
          'step--active': stage.active,
          'step--passed': stage.passed,
          'step--skipped': stage.skipped,
          'step--upcoming': stage.upcoming,
          'step--locked': stage.locked,
          'step--exit': stage.stageKind === 'exit',
          'step--reviewed': stage.reviewed,
        }"
      >
        <div class="step__rail">
          <span class="step__node"></span>
          <span v-if="idx < grouped.length - 1" class="step__line"></span>
        </div>
        <div class="step__body">
          <div class="step__top">
            <span class="step__code mono">{{ stage.code }}</span>
            <span class="step__scn mono" v-if="stage.scenarioType">
              {{ stage.scenarioType }}
            </span>
            <span v-if="stage.active" class="step__here mono">◀ 当前</span>
            <span v-else-if="stage.skipped" class="step__skipTag mono">已跳过</span>
            <button
              v-if="stage.reviewed"
              class="step__badge"
              title="查看阶段批阅"
              @click.stop="openReview(stage.scenarioType!)"
            >
              <span class="step__badgeDot"></span>已批阅
            </button>
          </div>
          <div class="step__label">{{ stage.label }}</div>
          <div class="step__state">{{ stage.display }}</div>
        </div>
      </li>
    </ol>

    <footer class="rail__foot">
      <p class="rail__legend muted">
        <span class="dot dot--passed"></span> 已过
        <span class="dot dot--active"></span> 当前
        <span class="dot dot--upcoming"></span> 未达
        <span class="dot dot--reviewed"></span> 已批阅
      </p>
    </footer>

    <StageReviewDrawer
      :event="drawerEvent"
      :open="drawerStage !== null"
      @close="drawerStage = null"
    />
  </nav>
</template>

<style scoped>
.rail {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.rail__head {
  margin-bottom: 14px;
}
.rail__kicker {
  font-family: var(--font-mono);
  font-size: 0.68rem;
  letter-spacing: 0.22em;
  color: var(--accent);
  margin: 0;
}
.rail__title {
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 1.3rem;
  font-weight: 700;
  margin: 4px 0 6px;
}
.rail__caption {
  font-size: 0.74rem;
  font-style: italic;
  margin: 0;
}
.rail__cat {
  display: inline-block;
  margin: 8px 0 0;
  padding: 1px 8px;
  font-size: 0.66rem;
  letter-spacing: 0.14em;
  color: var(--accent);
  border: 1px solid rgba(196, 71, 27, 0.5);
  border-radius: 2px;
}

/* 当前位置提示条（红字） */
.rail__now {
  margin: 0 0 10px;
  padding: 6px 10px;
  font-size: 0.76rem;
  color: var(--parchment-muted);
  background: rgba(196, 71, 27, 0.10);
  border: 1px solid rgba(196, 71, 27, 0.45);
  border-left: 3px solid var(--accent);
  border-radius: 3px;
}
.rail__now strong {
  color: var(--accent);
  font-weight: 600;
}
.rail__nowState {
  margin-left: 6px;
  font-size: 0.7rem;
  color: var(--parchment-dim);
}

.rail__list {
  list-style: none;
  margin: 0;
  padding: 0;
  flex: 1;
}

.step {
  position: relative;
  display: grid;
  grid-template-columns: 20px 1fr;
  gap: 10px;
  padding: 2px 0 10px;
}

.step__rail {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.step__node {
  width: 11px;
  height: 11px;
  border-radius: 50%;
  margin-top: 5px;
  background: var(--ink-700);
  border: 1px solid var(--line-strong);
  transition: all 0.25s ease;
  flex-shrink: 0;
}
.step__line {
  flex: 1;
  width: 1px;
  background: var(--line);
  margin-top: 2px;
}

.step--passed .step__node {
  background: var(--accent-muted);
  border-color: var(--accent-muted);
}
.step--passed .step__line {
  background: var(--accent-muted);
}
/* 被跳过的阶段：保持灰色 + 虚线节点 */
.step--skipped .step__node {
  background: transparent;
  border: 1px dashed var(--line-strong);
}
.step--skipped .step__label,
.step--skipped .step__state {
  opacity: 0.45;
}
.step--active .step__node {
  background: var(--accent);
  border-color: var(--accent);
  box-shadow:
    0 0 0 4px rgba(196, 71, 27, 0.18),
    0 0 16px rgba(196, 71, 27, 0.4);
}
.step--locked .step__node {
  opacity: 0.4;
}
/* 已批阅：节点加朱砂外环 */
.step--reviewed .step__node {
  box-shadow: 0 0 0 3px rgba(176, 138, 62, 0.25);
}

.step__body {
  padding-bottom: 4px;
}

.step__top {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
  flex-wrap: wrap;
}
.step__code {
  font-size: 0.7rem;
  color: var(--parchment-dim);
}
.step__scn {
  font-size: 0.6rem;
  padding: 1px 5px;
  border: 1px solid var(--line);
  border-radius: 2px;
  color: var(--accent-amber);
}
.step__here {
  font-size: 0.62rem;
  letter-spacing: 0.08em;
  color: var(--accent);
  animation: pulse-here 1.6s ease-in-out infinite;
}
.step__skipTag {
  font-size: 0.6rem;
  color: var(--parchment-faint);
  border: 1px dashed var(--line-strong);
  border-radius: 2px;
  padding: 0 4px;
}
@keyframes pulse-here {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}

/* 已批阅角标 — 卷宗批阅章 */
.step__badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 1px 7px;
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.64rem;
  letter-spacing: 0.06em;
  color: var(--accent-amber);
  background: rgba(176, 138, 62, 0.08);
  border: 1px solid rgba(176, 138, 62, 0.5);
  border-radius: 2px;
  cursor: pointer;
  transition: all 0.15s ease;
  animation: ink-rise 0.5s ease both;
}
.step__badge:hover {
  background: rgba(176, 138, 62, 0.18);
  border-color: var(--accent-amber);
}
.step__badgeDot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent-amber);
}

.step__label {
  font-family: var(--font-display);
  font-variation-settings: "opsz" 24, "wght" 500;
  font-size: 0.86rem;
  color: var(--parchment-muted);
  line-height: 1.3;
}
.step--active .step__label {
  color: var(--parchment);
  font-weight: 600;
}

.step__state {
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.78rem;
  color: var(--parchment-dim);
}
.step--active .step__state {
  color: var(--accent);
}

.step--locked .step__label,
.step--locked .step__state {
  opacity: 0.4;
}

/* 提前终止节点（撤案/不起诉/判决生效）— 灰绿虚线旁支样式 */
.step--exit {
  grid-template-columns: 22px 1fr;
}
.step--exit .step__node {
  background: transparent;
  border: 1px dashed var(--accent-success);
  opacity: 0.7;
}
.step--exit .step__line {
  background: repeating-linear-gradient(
    to bottom,
    var(--accent-success) 0 3px,
    transparent 3px 6px
  );
  opacity: 0.5;
}
.step--exit .step__label {
  color: var(--accent-success);
  font-style: italic;
  font-size: 0.8rem;
}
.step--exit .step__state {
  color: var(--parchment-faint);
  font-size: 0.72rem;
}

.rail__foot {
  margin-top: auto;
  padding-top: 16px;
  border-top: 1px dashed var(--line);
}
.rail__legend {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 0.72rem;
}
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 3px;
  vertical-align: middle;
}
.dot--passed { background: var(--accent-muted); }
.dot--active { background: var(--accent); }
.dot--upcoming { background: transparent; border: 1px solid var(--line-strong); }
.dot--reviewed {
  background: transparent;
  border: 1px solid var(--accent-amber);
}
</style>
