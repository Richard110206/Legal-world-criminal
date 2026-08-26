<script setup lang="ts">
import { computed, type DeepReadonly } from "vue";
import type { LearningEvent } from "../lib/types";

type RO<T> = DeepReadonly<T>;

const props = defineProps<{
  event: RO<LearningEvent> | LearningEvent | null;
  open: boolean;
}>();

const emit = defineEmits<{ close: [] }>();

const STAGE_NAMES: Record<string, string> = {
  LC: "委托洽谈",
  INV: "侦查阶段",
  PR: "审查起诉",
  DS: "辩护词起草",
  CR: "刑事一审",
  CRA: "刑事二审",
};

const stageName = computed(() => STAGE_NAMES[props.event?.stage ?? ""] ?? props.event?.stage ?? "");

/** 能力码 → 中文名（与后端 rubrics.py CAPABILITIES 对齐） */
const CAP_NAMES: Record<string, string> = {
  fact_identification: "事实识别",
  rule_retrieval: "规范检索",
  subsumption: "要件涵摄",
  claim_construction: "辩护主张构建",
  evidence_marshalling: "证据组织",
  evidentiary_advocacy: "质证对抗",
  position_consistency: "立场一致性",
  procedural_compliance: "程序合规",
};

const scoredCaps = computed(() => {
  const ev = props.event;
  if (!ev) return [];
  return Object.entries(ev.capability_scores ?? {})
    .filter(([, v]) => v && typeof v.score === "number")
    .map(([code, v]) => ({
      code,
      name: CAP_NAMES[code] ?? code,
      score: Math.max(0, Math.min(1, Number(v.score))),
      raw: typeof v.raw === "number" ? v.raw : Math.round(Number(v.score) * 10),
      rationale: v.rationale ?? "",
      evidence: v.evidence_quote ?? "",
    }))
    .sort((a, b) => b.score - a.score);
});

const meanScore = computed(() => {
  if (!scoredCaps.value.length) return 0;
  const sum = scoredCaps.value.reduce((acc, c) => acc + c.score, 0);
  return sum / scoredCaps.value.length;
});

const grade = computed(() => {
  const m = meanScore.value;
  if (m >= 0.85) return { label: "优", cls: "g--a" };
  if (m >= 0.7) return { label: "良", cls: "g--b" };
  if (m >= 0.55) return { label: "中", cls: "g--c" };
  return { label: "待改进", cls: "g--d" };
});

const subRows = computed(
  () => (props.event?.subsumption_table ?? []).filter((r) => r && r.element),
);

const conclusionCls = (c: string) => {
  if (c.includes("不符合") || c.includes("无罪")) return "c--neg";
  if (c.includes("存疑")) return "c--doubt";
  return "c--pos";
};

const citations = computed(() => props.event?.law_citations ?? []);
const citStatusName = (s: string) => {
  switch (s) {
    case "valid":
      return "正确";
    case "invalid_article":
    case "invalid_title":
    case "mismatch":
      return "有误";
    case "not_found":
      return "未匹配";
    default:
      return s || "—";
  }
};

const alignmentItems = computed(() => props.event?.citation_alignment ?? []);
const alignmentSummary = computed(() => props.event?.alignment_summary);
const alignVerdictName = (v: string) => {
  switch (v) {
    case "supports":
      return "支持";
    case "contradicts":
      return "矛盾";
    default:
      return "无关";
  }
};
</script>

<template>
  <Transition name="drawer">
    <section v-if="open && event" class="drawer" role="dialog" aria-label="阶段批阅报告">
      <header class="dr__head">
        <div class="dr__headMain">
          <p class="dr__kicker mono">STAGE REVIEW · 批阅卷宗</p>
          <h3 class="dr__title">
            {{ stageName }}
            <span class="dr__stageCode mono">{{ event.stage }}</span>
          </h3>
          <p class="dr__meta mono">
            {{ event.case_id }}
            <span v-if="event.charge"> · {{ event.charge }}</span>
            <span v-if="event.scored_at"> · {{ event.scored_at.slice(0, 10) }}</span>
          </p>
        </div>
        <div class="dr__grade" :class="grade.cls">
          <span class="dr__gradeNum">{{ (meanScore * 10).toFixed(1) }}</span>
          <span class="dr__gradeLabel">{{ grade.label }}</span>
        </div>
        <button class="dr__close" @click="emit('close')" aria-label="关闭">×</button>
      </header>

      <div class="dr__body">
        <!-- 总评 -->
        <section v-if="event.overall_feedback" class="blk blk--feedback">
          <p class="blk__label mono">OVERALL · 导师总评</p>
          <p class="blk__feedback">{{ event.overall_feedback }}</p>
        </section>

        <!-- 能力横条 -->
        <section class="blk">
          <p class="blk__label mono">CAPABILITIES · 能力评分</p>
          <ul class="caps">
            <li v-for="cap in scoredCaps" :key="cap.code" class="cap">
              <div class="cap__row">
                <span class="cap__name">{{ cap.name }}</span>
                <span class="cap__score mono" :class="{ 'cap__score--low': cap.score < 0.55 }">
                  {{ cap.raw }}/10
                </span>
              </div>
              <div class="cap__bar">
                <div
                  class="cap__fill"
                  :class="{ 'cap__fill--low': cap.score < 0.55, 'cap__fill--hi': cap.score >= 0.85 }"
                  :style="{ width: `${Math.round(cap.score * 100)}%` }"
                ></div>
              </div>
              <p v-if="cap.rationale" class="cap__why">{{ cap.rationale }}</p>
              <p v-if="cap.evidence" class="cap__ev">「{{ cap.evidence }}」</p>
            </li>
          </ul>
        </section>

        <!-- 涵摄三栏表 -->
        <section v-if="subRows.length" class="blk blk--sub">
          <p class="blk__label mono">SUBSUMPTION · 要件涵摄对照</p>
          <div class="subtable" role="table">
            <div class="subtable__row subtable__row--head" role="row">
              <span role="columnheader">构成要件</span>
              <span role="columnheader">案件事实</span>
              <span role="columnheader">涵摄结论</span>
            </div>
            <div
              v-for="(row, i) in subRows"
              :key="i"
              class="subtable__row"
              :class="{ 'subtable__row--alt': i % 2 === 1 }"
              role="row"
            >
              <span class="subtable__cell subtable__cell--el" role="cell">{{ row.element }}</span>
              <span class="subtable__cell" role="cell">{{ row.fact_found || "—" }}</span>
              <span class="subtable__cell" role="cell">
                <em class="sub__concl" :class="conclusionCls(row.conclusion)">
                  {{ row.conclusion || "—" }}
                </em>
                <span v-if="row.comment" class="sub__comment">{{ row.comment }}</span>
              </span>
            </div>
          </div>
        </section>

        <!-- 错误标记 -->
        <section v-if="event.error_tags?.length" class="blk blk--errs">
          <p class="blk__label mono">ERROR MARKS · 批注</p>
          <ul class="errs">
            <li v-for="(tag, i) in event.error_tags" :key="i" class="errs__tag">⚠ {{ tag }}</li>
          </ul>
        </section>

        <!-- 法条核验 -->
        <section v-if="citations.length" class="blk">
          <p class="blk__label mono">CITATIONS · 法条核验</p>
          <ul class="cits">
            <li v-for="(c, i) in citations" :key="i" class="cit-row">
              <span
                class="cit-row__status mono"
                :class="c.status === 'valid' ? 's--ok' : 's--bad'"
              >
                {{ citStatusName(c.status) }}
              </span>
              <div class="cit-row__body">
                <p class="cit-row__ref">{{ c.citation }}</p>
                <p v-if="c.issue" class="cit-row__issue">{{ c.issue }}</p>
                <blockquote v-if="c.content" class="cit-row__law">{{ c.content }}</blockquote>
              </div>
            </li>
          </ul>
        </section>

        <!-- 引用对齐核验（NLI） -->
        <section v-if="alignmentItems.length" class="blk">
          <p class="blk__label mono">CITATION ALIGNMENT · 引用对齐核验</p>
          <p v-if="alignmentSummary" class="aln__summary mono">
            支持 {{ alignmentSummary.supports ?? 0 }} / 矛盾 {{ alignmentSummary.contradicts ?? 0 }} /
            无关 {{ alignmentSummary.neutral ?? 0 }}
            <span v-if="alignmentSummary.model_layer" class="aln__dual">双层核验</span>
          </p>
          <ul class="alns">
            <li
              v-for="(a, i) in alignmentItems"
              :key="i"
              class="aln-row"
              :class="{ 'aln-row--bad': a.verdict === 'contradicts' }"
            >
              <span
                class="aln-row__verdict mono"
                :class="{
                  'v--ok': a.verdict === 'supports',
                  'v--bad': a.verdict === 'contradicts',
                  'v--neutral': a.verdict === 'neutral',
                }"
              >
                {{ alignVerdictName(a.verdict) }}
              </span>
              <div class="aln-row__body">
                <p class="aln-row__sentence">「{{ a.sentence }}」</p>
                <p class="aln-row__ref mono">{{ a.citation }}</p>
                <p v-if="a.reason" class="aln-row__reason">{{ a.reason }}</p>
                <p v-if="a.layer_conflict" class="aln-row__conflict mono">
                  ⚠ 模型层判定：{{ a.model_verdict === "supports" ? "支持" : a.model_verdict === "contradicts" ? "矛盾" : "无关" }}（两层分歧，采信裁判层）
                </p>
              </div>
            </li>
          </ul>
        </section>

        <!-- 知识缺口 -->
        <section v-if="event.knowledge_gaps?.length" class="blk">
          <p class="blk__label mono">KNOWLEDGE GAPS · 知识点欠缺</p>
          <ul class="gaps">
            <li v-for="(g, i) in event.knowledge_gaps" :key="i" class="gaps__item">{{ g }}</li>
          </ul>
        </section>
      </div>

      <footer class="dr__foot mono">
        <span>{{ event.event_id }}</span>
        <span v-if="event.gold_incomplete">※ 本案金标准不全，评分仅供参考</span>
      </footer>
    </section>
  </Transition>
</template>

<style scoped>
.drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(480px, 92vw);
  z-index: 60;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(ellipse at 100% 0%, rgba(196, 71, 27, 0.06), transparent 45%),
    linear-gradient(180deg, var(--ink-750), var(--ink-900));
  border-left: 1px solid var(--line-strong);
  box-shadow: -30px 0 80px -30px rgba(0, 0, 0, 0.85);
}

/* ── 头部 ── */
.dr__head {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 18px 22px 14px;
  border-bottom: 1px solid var(--line-strong);
  flex-shrink: 0;
}
.dr__headMain { flex: 1; min-width: 0; }
.dr__kicker {
  margin: 0 0 4px;
  font-size: 0.64rem;
  letter-spacing: 0.22em;
  color: var(--accent);
}
.dr__title {
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 1.25rem;
  font-weight: 700;
  margin: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}
.dr__stageCode {
  font-size: 0.66rem;
  padding: 1px 6px;
  border: 1px solid rgba(196, 71, 27, 0.5);
  border-radius: 2px;
  color: var(--accent);
}
.dr__meta {
  margin: 4px 0 0;
  font-size: 0.7rem;
  color: var(--parchment-dim);
}
.dr__grade {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 58px;
  height: 58px;
  border-radius: 50%;
  border: 1px solid var(--line-strong);
  flex-shrink: 0;
  transform: rotate(4deg);
  background: rgba(0, 0, 0, 0.25);
}
.dr__gradeNum {
  font-family: var(--font-display);
  font-size: 1.15rem;
  line-height: 1;
}
.dr__gradeLabel {
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.62rem;
  margin-top: 2px;
}
.g--a { border-color: rgba(122, 153, 98, 0.6); color: var(--accent-success); }
.g--b { border-color: rgba(176, 138, 62, 0.6); color: var(--accent-amber); }
.g--c { border-color: rgba(196, 71, 27, 0.5); color: var(--accent); }
.g--d { border-color: rgba(196, 71, 27, 0.8); color: var(--accent); }
.dr__close {
  position: absolute;
  top: 10px;
  right: 12px;
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  background: transparent;
  border: 1px solid var(--line);
  border-radius: 2px;
  color: var(--parchment-dim);
  font-size: 1.05rem;
  cursor: pointer;
}
.dr__close:hover { border-color: var(--accent); color: var(--accent); }

/* ── 滚动体 ── */
.dr__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 22px 20px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.blk__label {
  margin: 0 0 10px;
  font-size: 0.64rem;
  letter-spacing: 0.2em;
  color: var(--parchment-dim);
  padding-bottom: 5px;
  border-bottom: 1px dashed var(--line);
}

/* 总评 */
.blk--feedback {
  padding: 12px 14px;
  border: 1px solid rgba(196, 71, 27, 0.35);
  border-left: 3px solid var(--accent);
  border-radius: 3px;
  background: rgba(196, 71, 27, 0.05);
}
.blk__feedback {
  margin: 0;
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.92rem;
  line-height: 1.8;
  color: var(--parchment);
  white-space: pre-wrap;
}

/* 能力横条 */
.caps { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
.cap { animation: ink-rise 0.4s ease both; }
.cap__row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 4px;
}
.cap__name {
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.86rem;
  color: var(--parchment);
}
.cap__score { font-size: 0.78rem; color: var(--accent-amber); }
.cap__score--low { color: var(--accent); }
.cap__bar {
  height: 6px;
  background: rgba(0, 0, 0, 0.4);
  border: 1px solid var(--line);
  border-radius: 2px;
  overflow: hidden;
}
.cap__fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent-soft), var(--accent-amber));
  transition: width 0.7s cubic-bezier(0.2, 0.6, 0.2, 1);
}
.cap__fill--hi { background: linear-gradient(90deg, #5a7a45, var(--accent-success)); }
.cap__fill--low { background: linear-gradient(90deg, #7a2a12, var(--accent)); }
.cap__why {
  margin: 5px 0 0;
  font-size: 0.78rem;
  line-height: 1.55;
  color: var(--parchment-muted);
}
.cap__ev {
  margin: 2px 0 0;
  font-size: 0.75rem;
  line-height: 1.5;
  color: var(--parchment-dim);
  font-style: italic;
}

/* 涵摄三栏表 */
.blk--sub .blk__label { color: var(--accent); }
.subtable {
  border: 1px solid var(--line-strong);
  border-radius: 3px;
  overflow: hidden;
  font-size: 0.8rem;
}
.subtable__row {
  display: grid;
  grid-template-columns: 1fr 1.5fr 1.2fr;
}
.subtable__row--head {
  background: rgba(196, 71, 27, 0.10);
  border-bottom: 1px solid var(--line-strong);
}
.subtable__row--head span {
  padding: 7px 10px;
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--accent);
}
.subtable__row:not(.subtable__row--head) { border-bottom: 1px solid var(--line-faint); }
.subtable__row--alt { background: rgba(255, 255, 255, 0.015); }
.subtable__cell {
  padding: 8px 10px;
  line-height: 1.55;
  color: var(--parchment-muted);
  word-break: break-word;
}
.subtable__cell--el {
  font-family: "Noto Serif SC", var(--font-body);
  color: var(--parchment);
  font-weight: 600;
}
.sub__concl { font-style: normal; font-weight: 600; }
.c--pos { color: var(--accent-success); }
.c--neg { color: var(--accent); }
.c--doubt { color: var(--accent-amber); }
.sub__comment {
  display: block;
  margin-top: 3px;
  font-size: 0.72rem;
  color: var(--parchment-faint);
  line-height: 1.45;
}

/* 错误批注 */
.blk--errs .blk__label { color: var(--accent); }
.errs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.errs__tag {
  padding: 7px 11px;
  border: 1px solid rgba(196, 71, 27, 0.4);
  border-left: 3px solid var(--accent);
  border-radius: 2px;
  background: rgba(196, 71, 27, 0.06);
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.82rem;
  color: #e8a08b;
}

/* 法条核验 */
.cits { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.cit-row { display: flex; gap: 10px; }
.cit-row__status {
  flex-shrink: 0;
  width: 44px;
  text-align: center;
  padding: 2px 0;
  font-size: 0.68rem;
  border-radius: 2px;
  border: 1px solid;
  align-self: flex-start;
  margin-top: 2px;
}
.s--ok {
  color: var(--accent-success);
  border-color: rgba(122, 153, 98, 0.5);
  background: rgba(122, 153, 98, 0.08);
}
.s--bad {
  color: var(--accent);
  border-color: rgba(196, 71, 27, 0.5);
  background: rgba(196, 71, 27, 0.08);
}
.cit-row__body { flex: 1; min-width: 0; }
.cit-row__ref {
  margin: 0 0 2px;
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.84rem;
  color: var(--parchment);
}
.cit-row__issue { margin: 0 0 4px; font-size: 0.76rem; color: #d8bd85; }
.cit-row__law {
  margin: 0;
  padding: 5px 9px;
  background: rgba(0, 0, 0, 0.3);
  border-left: 2px solid var(--line-strong);
  border-radius: 2px;
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.78rem;
  line-height: 1.65;
  color: var(--parchment-muted);
}

/* 引用对齐核验（NLI） */
.aln__summary {
  margin: 0 0 10px;
  font-size: 0.72rem;
  color: var(--parchment-muted);
  letter-spacing: 0.04em;
}
.aln__dual {
  margin-left: 8px;
  padding: 1px 6px;
  border: 1px solid rgba(122, 153, 98, 0.45);
  border-radius: 2px;
  color: var(--accent-success);
  font-size: 0.64rem;
}
.alns { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.aln-row {
  display: flex;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 3px;
  background: rgba(0, 0, 0, 0.18);
}
.aln-row--bad {
  border-color: rgba(196, 71, 27, 0.45);
  background: rgba(196, 71, 27, 0.05);
}
.aln-row__verdict {
  flex-shrink: 0;
  width: 44px;
  text-align: center;
  padding: 2px 0;
  font-size: 0.68rem;
  border-radius: 2px;
  border: 1px solid;
  align-self: flex-start;
  margin-top: 2px;
}
.v--ok {
  color: var(--accent-success);
  border-color: rgba(122, 153, 98, 0.5);
  background: rgba(122, 153, 98, 0.08);
}
.v--bad {
  color: var(--accent);
  border-color: rgba(196, 71, 27, 0.5);
  background: rgba(196, 71, 27, 0.08);
}
.v--neutral {
  color: var(--parchment-muted);
  border-color: rgba(176, 138, 62, 0.4);
  background: rgba(176, 138, 62, 0.05);
}
.aln-row__body { flex: 1; min-width: 0; }
.aln-row__sentence {
  margin: 0 0 3px;
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.8rem;
  line-height: 1.6;
  color: var(--parchment);
}
.aln-row__ref { margin: 0 0 3px; font-size: 0.7rem; color: #d8bd85; }
.aln-row__reason { margin: 0; font-size: 0.74rem; line-height: 1.55; color: var(--parchment-muted); }
.aln-row__conflict { margin: 4px 0 0; font-size: 0.66rem; color: #d8bd85; }

/* 知识缺口 */
.gaps { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 6px; }
.gaps__item {
  padding: 3px 9px;
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.76rem;
  color: var(--parchment-muted);
  border: 1px dashed rgba(176, 138, 62, 0.55);
  border-radius: 2px;
  background: rgba(176, 138, 62, 0.05);
}

/* 脚注 */
.dr__foot {
  flex-shrink: 0;
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 22px;
  border-top: 1px dashed var(--line);
  font-size: 0.64rem;
  color: var(--parchment-faint);
}

/* 抽屉滑入 */
.drawer-enter-active,
.drawer-leave-active { transition: transform 0.35s cubic-bezier(0.2, 0.6, 0.2, 1), opacity 0.3s ease; }
.drawer-enter-from,
.drawer-leave-to { transform: translateX(40px); opacity: 0; }
</style>
