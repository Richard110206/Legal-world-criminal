<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api } from "../lib/api";
import type {
  AdaptiveAnswerResponse,
  AdaptivePlan,
  AdaptiveRecommendation,
} from "../lib/types";

const props = withDefaults(
  defineProps<{ mode?: "diagnostic" | "review" }>(),
  { mode: "review" },
);
const emit = defineEmits<{ (e: "back"): void }>();

const loading = ref(false);
const error = ref("");
const plan = ref<AdaptivePlan | null>(null);
const queueIndex = ref(0);
const selectedOption = ref("");
const submitting = ref(false);
const lastResult = ref<AdaptiveAnswerResponse | null>(null);
const answeredCount = ref(0);

const modeTitle = computed(() =>
  props.mode === "diagnostic" ? "预习 · 诊断测验" : "复习 · 弱点补强",
);
const modeKicker = computed(() =>
  props.mode === "diagnostic" ? "PREVIEW / DIAGNOSTIC" : "REVIEW / REMEDIATION",
);

const currentItem = computed<AdaptiveRecommendation | null>(() => {
  const recs = plan.value?.recommendations ?? [];
  return recs[queueIndex.value] ?? null;
});

const totalInQueue = computed(() => (plan.value?.recommendations ?? []).length);

const evidenceRows = computed(() =>
  (plan.value?.knowledge_evidence ?? [])
    .filter((row) => row.event_count > 0)
    .sort((a, b) => a.posterior_mean - b.posterior_mean),
);

async function loadPlan(): Promise<void> {
  loading.value = true;
  error.value = "";
  lastResult.value = null;
  selectedOption.value = "";
  try {
    plan.value = await api.adaptivePlan(props.mode, 5);
    queueIndex.value = 0;
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc);
  } finally {
    loading.value = false;
  }
}

async function submitAnswer(): Promise<void> {
  const item = currentItem.value;
  if (!item || !selectedOption.value || submitting.value) return;
  submitting.value = true;
  error.value = "";
  try {
    lastResult.value = await api.adaptiveAnswer(item.item_id, selectedOption.value);
    answeredCount.value += 1;
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : String(exc);
  } finally {
    submitting.value = false;
  }
}

function nextItem(): void {
  lastResult.value = null;
  selectedOption.value = "";
  if (queueIndex.value < totalInQueue.value - 1) {
    queueIndex.value += 1;
  } else {
    void loadPlan(); // queue exhausted → re-plan with fresh evidence
  }
}

function optionClass(key: string): string {
  if (!lastResult.value) {
    return selectedOption.value === key ? "opt--selected" : "";
  }
  const answer = lastResult.value.answer ?? [];
  if (answer.includes(key)) return "opt--correct";
  if (lastResult.value.selected === key) return "opt--wrong";
  return "";
}

onMounted(() => {
  void loadPlan();
});
</script>

<template>
  <div class="quiz">
    <header class="quiz__hdr">
      <div class="quiz__brand">
        <span class="quiz__kicker mono">{{ modeKicker }}</span>
        <h2 class="quiz__title">{{ modeTitle }}</h2>
      </div>
      <div class="quiz__meta mono">
        <span v-if="plan">已答 {{ answeredCount }} 题</span>
        <button class="btn btn--ghost" @click="emit('back')">返回案例工作台</button>
      </div>
    </header>

    <div v-if="error" class="quiz__error" role="alert">{{ error }}</div>

    <div class="quiz__body">
      <!-- 左：作答区 -->
      <section class="quiz__main">
        <div v-if="loading" class="quiz__placeholder">正在生成推荐…</div>

        <div v-else-if="!currentItem" class="quiz__placeholder">
          题库已答完，等待补充新题。
        </div>

        <article v-else class="qcard" :key="currentItem.item_id">
          <div class="qcard__tags">
            <span class="tag mono">{{ currentItem.knowledge_name }}</span>
            <span class="tag tag--dim mono">难度 {{ currentItem.difficulty }}</span>
            <span
              v-if="currentItem.case_weakness"
              class="tag tag--weak mono"
              :title="'精学案件暴露的知识弱点（' + currentItem.case_weakness + '）'"
            >
              案例弱点 · {{ currentItem.case_weakness === "missing" ? "未掌握" : "部分掌握" }}
            </span>
          </div>

          <p class="qcard__stem">{{ currentItem.stem }}</p>

          <div class="qcard__options">
            <button
              v-for="(text, key) in currentItem.options"
              :key="key"
              class="opt"
              :class="optionClass(String(key))"
              :disabled="!!lastResult"
              @click="!lastResult && (selectedOption = String(key))"
            >
              <span class="opt__key mono">{{ key }}</span>
              <span class="opt__text">{{ text }}</span>
            </button>
          </div>

          <!-- 推荐理由（答题前可见） -->
          <p v-if="!lastResult" class="qcard__reason">
            <span class="mono">为何推送此题</span> —— {{ currentItem.reason }}
          </p>

          <!-- 判分反馈 -->
          <div v-if="lastResult" class="verdict" :class="lastResult.correct ? 'verdict--ok' : 'verdict--bad'">
            <div class="verdict__head">
              <strong>{{ lastResult.correct ? "回答正确" : "回答错误" }}</strong>
              <span class="mono">
                正确答案：{{ lastResult.answer.join(" / ") }}
              </span>
            </div>
            <p class="verdict__rationale">{{ lastResult.rationale }}</p>
            <div v-if="lastResult.misconceptions_hit.length" class="verdict__miscon">
              <span class="mono">命中易错点</span>
              <ul>
                <li v-for="(m, i) in lastResult.misconceptions_hit" :key="i">{{ m }}</li>
              </ul>
            </div>
            <div v-if="lastResult.legal_basis.length" class="verdict__law">
              <div v-for="(law, i) in lastResult.legal_basis" :key="i" class="law">
                <span class="law__cite mono">《{{ law.law_name }}》{{ law.article }}</span>
                <p class="law__text">{{ law.text }}</p>
              </div>
            </div>
            <div class="verdict__actions">
              <button class="btn btn--primary" @click="nextItem">
                {{ queueIndex < totalInQueue - 1 ? "下一题" : "重新规划 · 继续" }}
              </button>
            </div>
          </div>

          <div v-else class="qcard__actions">
            <button
              class="btn btn--primary"
              :disabled="!selectedOption || submitting"
              @click="submitAnswer"
            >
              {{ submitting ? "提交中…" : "提交答案" }}
            </button>
          </div>
        </article>
      </section>

      <!-- 右：知识点掌握证据 -->
      <aside class="quiz__side">
        <h3 class="quiz__sideTitle">知识点掌握证据</h3>
        <p v-if="!evidenceRows.length" class="quiz__sideEmpty">
          还没有作答记录——完成诊断后这里会显示各知识点的掌握度估计。
        </p>
        <div v-for="row in evidenceRows" :key="row.knowledge_id" class="ev">
          <div class="ev__row">
            <span class="ev__name">{{ row.knowledge_name }}</span>
            <span class="ev__pct mono">{{ Math.round(row.posterior_mean * 100) }}%</span>
          </div>
          <div class="ev__bar">
            <span :style="{ width: Math.round(row.posterior_mean * 100) + '%' }" />
          </div>
          <span class="ev__status mono">{{ row.event_count }} 次作答 · {{ row.evidence_status }}</span>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.quiz {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.quiz__hdr {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 28px;
  border-bottom: 1px solid var(--line-strong);
  background: linear-gradient(180deg, var(--ink-800), var(--ink-850, var(--ink-800)));
}
.quiz__kicker {
  font-size: 0.62rem;
  letter-spacing: 0.22em;
  color: var(--parchment-dim);
}
.quiz__title {
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--parchment);
  margin-top: 2px;
}
.quiz__meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 0.76rem;
  color: var(--parchment-muted);
}
.quiz__error {
  margin: 12px 28px 0;
  padding: 10px 14px;
  border: 1px solid rgba(196, 71, 27, 0.5);
  color: #f0b6a6;
  font-size: 0.85rem;
}
.quiz__body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 0;
}
.quiz__main {
  min-width: 0;
  overflow-y: auto;
  padding: 26px 34px 40px;
}
.quiz__placeholder {
  color: var(--parchment-dim);
  font-size: 0.9rem;
  padding: 40px 0;
  text-align: center;
}
.quiz__side {
  border-left: 1px solid var(--line);
  background: linear-gradient(180deg, rgba(20, 17, 11, 0.92), rgba(14, 12, 8, 0.96));
  padding: 16px;
  overflow-y: auto;
}
.quiz__sideTitle {
  font-family: var(--font-display);
  font-size: 0.9rem;
  color: var(--accent-amber);
  margin-bottom: 12px;
}
.quiz__sideEmpty {
  font-size: 0.78rem;
  color: var(--parchment-dim);
  line-height: 1.6;
}

/* 题卡 */
.qcard {
  max-width: 780px;
  margin: 0 auto;
  border: 1px solid var(--line-strong);
  background: linear-gradient(180deg, var(--ink-750, #1c1913), var(--ink-900));
  padding: 24px 28px;
}
.qcard__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
}
.tag {
  font-size: 0.66rem;
  padding: 3px 10px;
  border: 1px solid var(--line-strong);
  color: var(--parchment-muted);
  letter-spacing: 0.06em;
}
.tag--weak {
  border-color: rgba(196, 71, 27, 0.55);
  color: var(--accent);
}
.qcard__stem {
  font-family: "Noto Serif SC", serif;
  font-size: 1.02rem;
  line-height: 1.8;
  color: var(--parchment);
  margin-bottom: 18px;
}
.qcard__options {
  display: grid;
  gap: 10px;
}
.opt {
  display: flex;
  gap: 12px;
  align-items: baseline;
  text-align: left;
  padding: 12px 14px;
  background: transparent;
  border: 1px solid var(--line);
  color: var(--parchment-muted);
  cursor: pointer;
  font-size: 0.92rem;
  line-height: 1.5;
  transition: border-color 0.15s, background 0.15s;
}
.opt:hover:not(:disabled) {
  border-color: var(--accent-amber);
}
.opt--selected {
  border-color: var(--accent-amber);
  background: rgba(176, 138, 62, 0.08);
  color: var(--parchment);
}
.opt--correct {
  border-color: var(--accent-success, #7a9962);
  background: rgba(122, 153, 98, 0.1);
  color: var(--parchment);
}
.opt--wrong {
  border-color: var(--accent);
  background: rgba(196, 71, 27, 0.1);
}
.opt__key {
  font-size: 0.8rem;
  color: var(--accent-amber);
  flex-shrink: 0;
}
.qcard__reason {
  margin-top: 16px;
  font-size: 0.8rem;
  color: var(--parchment-dim);
  border-top: 1px dashed var(--line);
  padding-top: 12px;
}
.qcard__reason .mono {
  color: var(--accent-amber);
  letter-spacing: 0.1em;
  font-size: 0.66rem;
}
.qcard__actions,
.verdict__actions {
  margin-top: 20px;
  display: flex;
  justify-content: flex-end;
}

/* 判分 */
.verdict {
  margin-top: 18px;
  border: 1px solid var(--line-strong);
  padding: 16px 18px;
}
.verdict--ok { border-color: rgba(122, 153, 98, 0.5); }
.verdict--bad { border-color: rgba(196, 71, 27, 0.55); }
.verdict__head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 10px;
}
.verdict--ok .verdict__head strong { color: var(--accent-success, #7a9962); }
.verdict--bad .verdict__head strong { color: var(--accent); }
.verdict__head .mono {
  font-size: 0.74rem;
  color: var(--parchment-dim);
}
.verdict__rationale {
  font-size: 0.88rem;
  line-height: 1.7;
  color: var(--parchment-muted);
}
.verdict__miscon {
  margin-top: 12px;
  font-size: 0.82rem;
  color: #f0b6a6;
}
.verdict__miscon .mono {
  font-size: 0.64rem;
  letter-spacing: 0.1em;
  color: var(--accent);
}
.verdict__miscon ul {
  margin: 6px 0 0;
  padding-left: 18px;
}
.verdict__miscon li { margin-bottom: 4px; }
.verdict__law { margin-top: 12px; }
.law {
  border-left: 2px solid var(--accent-amber);
  padding: 6px 12px;
  margin-bottom: 8px;
  background: rgba(176, 138, 62, 0.05);
}
.law__cite {
  font-size: 0.72rem;
  color: var(--accent-amber);
}
.law__text {
  font-size: 0.78rem;
  line-height: 1.6;
  color: var(--parchment-dim);
  margin-top: 4px;
}

/* 侧栏证据 */
.ev { margin-bottom: 14px; }
.ev__row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.ev__name {
  font-size: 0.78rem;
  color: var(--parchment-muted);
}
.ev__pct {
  font-size: 0.74rem;
  color: var(--accent-amber);
}
.ev__bar {
  height: 4px;
  background: var(--ink-900, #12100b);
  border: 1px solid var(--line);
  margin: 5px 0 3px;
}
.ev__bar span {
  display: block;
  height: 100%;
  background: var(--accent-amber);
}
.ev__status {
  font-size: 0.64rem;
  color: var(--parchment-dim);
}

@media (max-width: 1020px) {
  .quiz__body { grid-template-columns: 1fr; }
  .quiz__side { display: none; }
}
</style>
