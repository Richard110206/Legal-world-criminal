<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api } from "../lib/api";
import { useSession } from "../composables/useSession";
import type { TeachingReport } from "../lib/types";

const session = useSession();
const report = ref<TeachingReport | null>(null);
const loading = ref(true);
const loadError = ref<string | null>(null);

async function load() {
  loading.value = true;
  loadError.value = null;
  try {
    const me = await api.me();
    const sid = String(me.id ?? "").trim() || "anonymous";
    report.value = await api.teachingReport(sid);
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : String(err);
  } finally {
    loading.value = false;
  }
}

onMounted(load);

/* ── 雷达图几何（8 轴内联 SVG） ──
   viewBox 左右各留 48px 标签余量，雷达本体半径 100（略小于初版 108），
   保证「立场一致性」「辩护主张」等 4-5 字轴标签完整呈现。 */
const RADIUS = 100;
const CENTER = 136;
const LABEL_FACTOR = 1.14;
const VIEW_BOX = "-48 -8 368 286";
const RINGS = [0.25, 0.5, 0.75, 1];

function axisPoint(index: number, ratio: number) {
  const total = report.value?.capability_radar?.length || 8;
  const angle = (Math.PI * 2 * index) / total - Math.PI / 2;
  const r = RADIUS * Math.max(0, Math.min(1, ratio));
  return {
    x: CENTER + r * Math.cos(angle),
    y: CENTER + r * Math.sin(angle),
  };
}

const ringPolygons = RINGS.map((ring) =>
  Array.from({ length: 8 }, (_, i) => axisPoint(i, ring)),
);

const radarPoints = computed(() => {
  const caps = report.value?.capability_radar ?? [];
  return caps.map((c, i) => ({ ...axisPoint(i, c.score), cap: c }));
});

const radarShape = computed(() =>
  radarPoints.value.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" "),
);

const radarEmpty = computed(() =>
  (report.value?.capability_radar ?? []).every((c) => c.score <= 0),
);

const labelAnchor = (i: number) => {
  const total = 8;
  const angle = (Math.PI * 2 * i) / total - Math.PI / 2;
  const cos = Math.cos(angle);
  if (Math.abs(cos) < 0.3) return "middle";
  return cos > 0 ? "start" : "end";
};
const labelDx = (i: number) => {
  const total = 8;
  const angle = (Math.PI * 2 * i) / total - Math.PI / 2;
  const cos = Math.cos(angle);
  return Math.abs(cos) < 0.3 ? 0 : cos > 0 ? 10 : -10;
};

/* ── 成长曲线（SVG 折线） ── */
const GW = 520;
const GH = 120;
const PAD = 18;

const growthPoints = computed(() => {
  const curve = report.value?.growth_curve ?? [];
  if (!curve.length) return [];
  const step = curve.length > 1 ? (GW - PAD * 2) / (curve.length - 1) : 0;
  return curve.map((g, i) => ({
    x: PAD + step * i,
    y: GH - PAD - Math.max(0, Math.min(1, Number(g.mean) || 0)) * (GH - PAD * 2),
    g,
  }));
});

const growthPath = computed(() =>
  growthPoints.value.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" "),
);

const growthArea = computed(() => {
  const pts = growthPoints.value;
  if (!pts.length) return "";
  const base = GH - PAD;
  return `M${pts[0].x.toFixed(1)},${base} ` +
    pts.map((p) => `L${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ") +
    ` L${pts[pts.length - 1].x.toFixed(1)},${base} Z`;
});

const STAGE_SHORT: Record<string, string> = {
  LC: "委托",
  INV: "侦查",
  PR: "审诉",
  DS: "辩护",
  CR: "一审",
  CRA: "二审",
};

const hasData = computed(() =>
  (report.value?.growth_curve?.length ?? 0) > 0 ||
  (report.value?.capability_radar ?? []).some((c) => c.score > 0),
);
</script>

<template>
  <div class="dossier-layer" @click.self="$emit('close')">
    <section class="dossier" role="dialog" aria-label="学习档案总报告">
      <header class="dos__head">
        <div>
          <p class="dos__kicker mono">LEARNING DOSSIER · 学习档案</p>
          <h2 class="dos__title">学期辩护能力报告</h2>
          <p class="dos__meta mono">
            {{ session.state.email }}
            <template v-if="report?.updated_at"> · 更新于 {{ report.updated_at.slice(0, 10) }}</template>
            <template v-if="report?.cases_played?.length"> · 已办 {{ report.cases_played.length }} 案</template>
          </p>
        </div>
        <button class="dos__close" @click="$emit('close')" aria-label="关闭">×</button>
      </header>

      <div v-if="loading" class="dos__state">调取档案中……</div>
      <div v-else-if="loadError" class="dos__state dos__state--err">
        档案调取失败：{{ loadError }}
        <button class="btn btn--ghost" @click="load">重试</button>
      </div>

      <div v-else-if="!hasData" class="dos__state">
        尚无批阅记录——完成一个案件阶段后，这里会呈现你的八维能力画像。
      </div>

      <div v-else class="dos__body">
        <!-- 左：雷达 + 成长 -->
        <div class="dos__col dos__col--viz">
          <section class="dos__blk">
            <p class="dos__label mono">CAPABILITY RADAR · 八维能力</p>
            <svg
              class="radar"
              :viewBox="VIEW_BOX"
              role="img"
              aria-label="八维能力雷达图"
            >
              <!-- 网格环 -->
              <polygon
                v-for="(ring, ri) in ringPolygons"
                :key="ri"
                :points="ring.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')"
                class="radar__ring"
              />
              <!-- 轴线 -->
              <line
                v-for="i in 8"
                :key="`ax-${i}`"
                :x1="CENTER"
                :y1="CENTER"
                :x2="axisPoint(i - 1, 1).x"
                :y2="axisPoint(i - 1, 1).y"
                class="radar__axis"
              />
              <!-- 能力面 -->
              <polygon
                v-if="!radarEmpty"
                :points="radarShape"
                class="radar__shape"
              />
              <!-- 顶点 -->
              <circle
                v-for="(p, i) in radarPoints"
                :key="`pt-${i}`"
                :cx="p.x"
                :cy="p.y"
                r="3"
                class="radar__dot"
              />
              <!-- 轴标签 -->
              <text
                v-for="(p, i) in radarPoints"
                :key="`lb-${i}`"
                :x="axisPoint(i, LABEL_FACTOR).x"
                :y="axisPoint(i, LABEL_FACTOR).y + 4"
                :text-anchor="labelAnchor(i)"
                :dx="labelDx(i)"
                class="radar__label"
              >
                {{ p.cap.name }}
              </text>
              <text
                v-for="(p, i) in radarPoints"
                :key="`sc-${i}`"
                :x="axisPoint(i, LABEL_FACTOR).x"
                :y="axisPoint(i, LABEL_FACTOR).y + 17"
                :text-anchor="labelAnchor(i)"
                :dx="labelDx(i)"
                class="radar__score"
              >
                {{ (p.cap.score * 10).toFixed(1) }}
              </text>
            </svg>
          </section>

          <section v-if="growthPoints.length" class="dos__blk">
            <p class="dos__label mono">GROWTH · 成长曲线</p>
            <svg class="growth" :viewBox="`0 0 ${GW} ${GH}`" role="img" aria-label="能力成长曲线">
              <line :x1="PAD" :y1="GH - PAD" :x2="GW - PAD" :y2="GH - PAD" class="growth__axis" />
              <line :x1="PAD" :y1="PAD" :x2="PAD" :y2="GH - PAD" class="growth__axis" />
              <path v-if="growthArea" :d="growthArea" class="growth__area" />
              <path v-if="growthPath" :d="growthPath" class="growth__line" />
              <g v-for="(p, i) in growthPoints" :key="`gp-${i}`" class="growth__pt">
                <circle :cx="p.x" :cy="p.y" r="3.5" />
                <title>{{ p.g.at?.slice(0, 10) }} · {{ STAGE_SHORT[p.g.stage] ?? p.g.stage }} · 均分 {{ (p.g.mean * 10).toFixed(1) }}</title>
              </g>
            </svg>
            <div class="growth__ticks mono">
              <span
                v-for="(p, i) in growthPoints"
                :key="`gt-${i}`"
                class="growth__tick"
              >
                {{ STAGE_SHORT[p.g.stage] ?? p.g.stage }}
              </span>
            </div>
          </section>
        </div>

        <!-- 右：缺口 / 批注 / 推荐 -->
        <div class="dos__col">
          <section v-if="report?.top_errors?.length" class="dos__blk">
            <p class="dos__label mono">RECURRING MARKS · 高频批注</p>
            <ul class="errs">
              <li v-for="e in report.top_errors.slice(0, 5)" :key="e.tag" class="errs__row">
                <span class="errs__tag">{{ e.tag }}</span>
                <span class="errs__count mono">×{{ e.count }}</span>
              </li>
            </ul>
          </section>

          <section v-if="report?.knowledge_gaps?.length" class="dos__blk">
            <p class="dos__label mono">KNOWLEDGE GAPS · 知识缺口</p>
            <ul class="gaps">
              <li v-for="g in report.knowledge_gaps.slice(0, 8)" :key="g.kp" class="gaps__row">
                <span class="gaps__kp">{{ g.kp }}</span>
                <span class="gaps__n mono">考查 {{ g.exposed }} 次</span>
              </li>
            </ul>
          </section>

          <section v-if="report?.recommendations?.length" class="dos__blk">
            <p class="dos__label mono">RECOMMENDED · 补强练习</p>
            <ol class="recs">
              <li v-for="(r, i) in report.recommendations" :key="i" class="recs__item">
                <div class="recs__head">
                  <span class="recs__no mono">{{ String(i + 1).padStart(2, "0") }}</span>
                  <span class="recs__chapter">{{ r.chapter }}</span>
                  <span v-if="r.question_type" class="recs__type mono">{{ r.question_type }}</span>
                </div>
                <p class="recs__q">{{ r.question }}</p>
                <div class="recs__kps">
                  <span v-for="kp in r.knowledge_points" :key="kp" class="recs__kp">{{ kp }}</span>
                </div>
              </li>
            </ol>
          </section>

          <section
            v-if="!report?.top_errors?.length && !report?.knowledge_gaps?.length && !report?.recommendations?.length"
            class="dos__blk"
          >
            <p class="dos__label mono">REMARKS</p>
            <p class="dos__emptyIn">暂无高频批注与知识缺口——继续保持。</p>
          </section>
        </div>
      </div>

      <footer class="dos__foot mono">
        <span>LegalWorld · 教学评分 v1</span>
        <span>八维框架：CJ-Bench 刑法化</span>
      </footer>
    </section>
  </div>
</template>

<script lang="ts">
export default { name: "LearningDossier" };
</script>

<style scoped>
.dossier-layer {
  position: fixed;
  inset: 0;
  z-index: 70;
  display: grid;
  place-items: center;
  padding: 32px;
  background: rgba(8, 6, 3, 0.72);
  backdrop-filter: blur(3px);
  animation: ink-rise 0.3s ease both;
}

.dossier {
  width: min(960px, 96vw);
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(ellipse at 15% 0%, rgba(196, 71, 27, 0.07), transparent 45%),
    linear-gradient(180deg, var(--ink-750), var(--ink-900));
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-lg);
  box-shadow: 0 40px 120px -40px rgba(0, 0, 0, 0.9);
  overflow: hidden;
}

/* ── 头 ── */
.dos__head {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 26px 14px;
  border-bottom: 1px solid var(--line-strong);
}
.dos__kicker {
  margin: 0 0 4px;
  font-size: 0.64rem;
  letter-spacing: 0.22em;
  color: var(--accent);
}
.dos__title {
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 1.5rem;
  font-weight: 700;
  margin: 0;
}
.dos__meta {
  margin: 4px 0 0;
  font-size: 0.72rem;
  color: var(--parchment-dim);
}
.dos__close {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  background: transparent;
  border: 1px solid var(--line);
  border-radius: 2px;
  color: var(--parchment-dim);
  font-size: 1.15rem;
  cursor: pointer;
}
.dos__close:hover { border-color: var(--accent); color: var(--accent); }

/* ── 状态 ── */
.dos__state {
  padding: 60px 30px;
  text-align: center;
  color: var(--parchment-muted);
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.95rem;
  display: flex;
  flex-direction: column;
  gap: 14px;
  align-items: center;
}
.dos__state--err { color: #e8a08b; }

/* ── 体 ── */
.dos__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: grid;
  grid-template-columns: minmax(320px, 5fr) minmax(300px, 4fr);
  gap: 22px;
  padding: 20px 26px;
}
.dos__col { display: flex; flex-direction: column; gap: 20px; min-width: 0; }

.dos__blk {
  animation: ink-rise 0.5s ease both;
}
.dos__label {
  margin: 0 0 10px;
  font-size: 0.64rem;
  letter-spacing: 0.2em;
  color: var(--parchment-dim);
  padding-bottom: 5px;
  border-bottom: 1px dashed var(--line);
}

/* 雷达图 */
.radar { width: 100%; max-width: 420px; display: block; margin: 0 auto; }
.radar__ring {
  fill: none;
  stroke: var(--line);
  stroke-width: 1;
}
.radar__axis {
  stroke: var(--line-faint);
  stroke-width: 1;
}
.radar__shape {
  fill: rgba(196, 71, 27, 0.18);
  stroke: var(--accent);
  stroke-width: 1.6;
  stroke-linejoin: round;
}
.radar__dot { fill: var(--accent); }
.radar__label {
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 11.5px;
  fill: var(--parchment-muted);
}
.radar__score {
  font-family: var(--font-mono);
  font-size: 9.5px;
  fill: var(--accent-amber);
}

/* 成长曲线 */
.growth { width: 100%; display: block; }
.growth__axis { stroke: var(--line-strong); stroke-width: 1; }
.growth__area {
  fill: rgba(176, 138, 62, 0.12);
  stroke: none;
}
.growth__line {
  fill: none;
  stroke: var(--accent-amber);
  stroke-width: 1.6;
  stroke-linejoin: round;
  stroke-linecap: round;
}
.growth__pt circle { fill: var(--accent-amber); stroke: var(--ink-900); stroke-width: 1.5; }
.growth__ticks {
  display: flex;
  justify-content: space-between;
  padding: 4px 18px 0;
  font-size: 0.64rem;
  color: var(--parchment-dim);
}

/* 高频批注 */
.errs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.errs__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 10px;
  border: 1px solid rgba(196, 71, 27, 0.3);
  border-left: 3px solid var(--accent);
  border-radius: 2px;
  background: rgba(196, 71, 27, 0.05);
}
.errs__tag {
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.8rem;
  color: #e8a08b;
}
.errs__count { font-size: 0.74rem; color: var(--accent); flex-shrink: 0; }

/* 知识缺口 */
.gaps { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 5px; }
.gaps__row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 5px 10px;
  border: 1px dashed rgba(176, 138, 62, 0.45);
  border-radius: 2px;
  background: rgba(176, 138, 62, 0.04);
}
.gaps__kp {
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.82rem;
  color: var(--parchment-muted);
}
.gaps__n { font-size: 0.68rem; color: var(--parchment-faint); flex-shrink: 0; }

/* 推荐练习 */
.recs {
  list-style: none;
  margin: 0;
  padding: 0;
  counter-reset: rec;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.recs__item {
  padding: 9px 12px;
  border: 1px solid var(--line);
  border-radius: 3px;
  background: rgba(0, 0, 0, 0.2);
  transition: border-color 0.15s ease;
}
.recs__item:hover { border-color: var(--line-strong); }
.recs__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 4px;
}
.recs__no { font-size: 0.68rem; color: var(--accent); }
.recs__chapter {
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.74rem;
  color: var(--parchment-dim);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.recs__type {
  font-size: 0.62rem;
  padding: 0 5px;
  border: 1px solid var(--line);
  border-radius: 2px;
  color: var(--accent-amber);
  flex-shrink: 0;
}
.recs__q {
  margin: 0 0 6px;
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.84rem;
  line-height: 1.6;
  color: var(--parchment);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.recs__kps { display: flex; flex-wrap: wrap; gap: 5px; }
.recs__kp {
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.66rem;
  padding: 1px 7px;
  color: var(--accent-cool);
  border: 1px solid rgba(92, 122, 138, 0.45);
  border-radius: 2px;
  background: rgba(92, 122, 138, 0.07);
}

.dos__emptyIn {
  margin: 0;
  font-size: 0.85rem;
  color: var(--parchment-dim);
  font-style: italic;
}

/* 脚 */
.dos__foot {
  flex-shrink: 0;
  display: flex;
  justify-content: space-between;
  padding: 9px 26px;
  border-top: 1px solid var(--line);
  font-size: 0.64rem;
  color: var(--parchment-faint);
}

@media (max-width: 860px) {
  .dos__body { grid-template-columns: 1fr; }
}
</style>
