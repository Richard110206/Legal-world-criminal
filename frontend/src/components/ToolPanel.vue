<script setup lang="ts">
import { computed, ref } from "vue";
import { useSession } from "../composables/useSession";
import { agentDisplayName } from "../lib/roleNames";

const session = useSession();

interface RetrievalHit {
  title: string;
  article?: string;
  effect?: string;
  status?: string;
  content_preview?: string;
}

interface RetrievalEvent {
  tool: string;
  query: string;
  hit_count: number;
  hits: RetrievalHit[];
}

interface ToolCallEntry {
  id: string;
  agentName: string;
  stage: string;
  tools: string[];
  skills: string[];
  retrieval: RetrievalEvent[];
  occurred_at: number;
}

/** 从事件流提取工具调用（后端 runtime_tech_used 推送） */
const toolCalls = computed<ToolCallEntry[]>(() => {
  const out: ToolCallEntry[] = [];
  for (const evt of session.events.value) {
    if (evt.type !== "runtime_progress") continue;
    const raw = evt.raw;
    if (!raw || String(raw.phase ?? "") !== "runtime_tech_used") continue;
    const tools = Array.isArray(raw.tool_names) ? raw.tool_names.map(String) : [];
    const skills = Array.isArray(raw.skill_names) ? raw.skill_names.map(String) : [];
    const retrievalRaw = Array.isArray(raw.retrieval_events) ? raw.retrieval_events : [];
    const retrieval: RetrievalEvent[] = retrievalRaw
      .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
      .map((item) => ({
        tool: String(item.tool ?? ""),
        query: String(item.query ?? ""),
        hit_count: Number(item.hit_count ?? 0),
        hits: Array.isArray(item.hits)
          ? item.hits
              .filter((h): h is Record<string, unknown> => typeof h === "object" && h !== null)
              .map((h) => ({
                title: String(h.title ?? ""),
                article: h.article ? String(h.article) : undefined,
                effect: h.effect ? String(h.effect) : undefined,
                status: h.status ? String(h.status) : undefined,
                content_preview: h.content_preview ? String(h.content_preview) : undefined,
              }))
          : [],
      }));
    if (!tools.length && !skills.length) continue;
    out.push({
      id: evt.id,
      agentName: agentDisplayName(String(raw.agent_name ?? raw.detail ?? ""), String(raw.agent_id ?? "")),
      stage: String(raw.stage ?? raw.scenario_type ?? ""),
      tools,
      skills,
      retrieval,
      occurred_at: evt.occurred_at,
    });
  }
  return out;
});

/** 展示最近 N 条（面板内可滚动） */
const displayCalls = computed(() => toolCalls.value.slice(-40));

const currentStageTools = computed(() => {
  const calls = toolCalls.value;
  if (!calls.length) return [];
  return calls[calls.length - 1].tools;
});

function stamp(ts: number): string {
  return new Date(ts).toLocaleTimeString("en-GB", { hour12: false });
}

/** 工具名 → 中文说明 */
const TOOL_LABELS: Record<string, string> = {
  load_skill: "加载技能",
  draft_defense_opinion_document: "起草辩护词",
  draft_indictment_document: "起草起诉书",
  draft_public_prosecution_document: "起草公诉词",
  draft_first_instance_criminal_judgment: "起草刑事一审判决书",
  draft_second_instance_criminal_judgment: "起草刑事二审判决书",
  search_yuandian_law: "法条检索（元典）",
  search_yuandian_law_detail: "法条溯源核验",
  search_yuandian_case: "类案检索（元典）",
  search_laws: "法条检索",
  search_cases: "类案检索",
  check_citations: "引用核验",
  search_legal_basis: "检索法律依据",
  statute_lookup: "刑法条文检索",
  precedent_search: "类案检索",
  case_file_read: "阅卷",
  memory_query: "记忆查询",
};

function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name;
}

const expanded = ref<Set<string>>(new Set());

function toggleExpand(id: string): void {
  const next = new Set(expanded.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  expanded.value = next;
}
</script>

<template>
  <section class="tools">
    <header class="tools__head">
      <p class="kicker">TOOL USAGE</p>
      <h3>工具调用</h3>
      <span class="tag tools__count">{{ displayCalls.length }}</span>
    </header>

    <div v-if="!displayCalls.length" class="tools__empty muted">
      尚无工具调用记录——模拟运行中,各角色的检索/起草/技能调用会实时显示在这里。
    </div>

    <div v-else class="tools__list">
      <article
        v-for="call in [...displayCalls].reverse()"
        :key="call.id"
        class="call"
        :class="{ 'call--retrieval': call.retrieval.length }"
      >
        <header class="call__head">
          <span class="call__agent">{{ call.agentName }}</span>
          <span v-if="call.stage" class="tag tag--amber">{{ call.stage }}</span>
          <span class="call__time mono">{{ stamp(call.occurred_at) }}</span>
        </header>
        <div class="call__items">
          <span v-for="tool in call.tools" :key="'t-' + tool" class="call__chip call__chip--tool" :title="tool">
            {{ toolLabel(tool) }}
          </span>
          <span v-for="skill in call.skills" :key="'s-' + skill" class="call__chip call__chip--skill" :title="skill">
            {{ skill }}
          </span>
        </div>

        <div v-if="call.retrieval.length" class="retrieval">
          <div
            v-for="(evt, ri) in call.retrieval"
            :key="call.id + '-r-' + ri"
            class="retrieval__event"
          >
            <button
              type="button"
              class="retrieval__toggle"
              @click="toggleExpand(call.id + '-r-' + ri)"
            >
              <span class="retrieval__tool">{{ toolLabel(evt.tool) || evt.tool }}</span>
              <span class="retrieval__query" :title="evt.query">检索词：{{ evt.query || '—' }}</span>
              <span class="retrieval__count">{{ evt.hit_count }} 条命中</span>
              <span class="retrieval__arrow">{{ expanded.has(call.id + '-r-' + ri) ? '收起' : '展开' }}</span>
            </button>
            <div v-if="expanded.has(call.id + '-r-' + ri)" class="retrieval__hits">
              <div v-if="!evt.hits.length" class="retrieval__empty">未命中相关条目</div>
              <article v-for="(hit, hi) in evt.hits" :key="hi" class="lawcard">
                <header class="lawcard__head">
                  <span class="lawcard__title">{{ hit.title }}</span>
                  <span v-if="hit.article" class="lawcard__article mono">{{ hit.article }}</span>
                </header>
                <div class="lawcard__meta">
                  <span v-if="hit.effect" class="lawcard__meta-item">效力：{{ hit.effect }}</span>
                  <span v-if="hit.status" class="lawcard__meta-item">时效：{{ hit.status }}</span>
                </div>
                <p v-if="hit.content_preview" class="lawcard__content">{{ hit.content_preview }}</p>
              </article>
            </div>
          </div>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.tools {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
}

.tools__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--line);
  flex-wrap: wrap;
}
.kicker {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 0.62rem;
  letter-spacing: 0.2em;
  color: var(--accent);
  flex-basis: 100%;
}
.tools__head h3 {
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 1.02rem;
  font-weight: 600;
  margin: 0;
  flex: 1;
}
.tools__count { font-size: 0.7rem; }

.tools__empty {
  font-size: 0.8rem;
  font-style: italic;
  text-align: center;
  padding: 18px 8px;
  line-height: 1.6;
}

.tools__list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-right: 4px;
}

.call {
  padding: 8px 10px;
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid var(--line);
  border-radius: var(--radius);
}

.call__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.call__agent {
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.86rem;
  font-weight: 600;
  color: var(--parchment);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.call__time {
  font-size: 0.66rem;
  color: var(--parchment-dim);
}

.call__items {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.call__chip {
  font-family: var(--font-mono);
  font-size: 0.68rem;
  padding: 2px 8px;
  border-radius: 2px;
  border: 1px solid var(--line-strong);
  color: var(--parchment-muted);
  background: rgba(255, 255, 255, 0.02);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.call__chip--tool {
  color: var(--accent-amber);
  border-color: rgba(176, 138, 62, 0.4);
  background: rgba(176, 138, 62, 0.07);
}
.call__chip--skill {
  color: var(--accent-cool);
  border-color: rgba(92, 122, 138, 0.4);
  background: rgba(92, 122, 138, 0.08);
}

/* ── 检索可视化（法条命中卡片）── */
.call--retrieval {
  border-color: rgba(176, 138, 62, 0.35);
}
.retrieval {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.retrieval__toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 5px 8px;
  background: rgba(176, 138, 62, 0.06);
  border: 1px solid rgba(176, 138, 62, 0.3);
  border-radius: 3px;
  color: inherit;
  font: inherit;
  cursor: pointer;
  text-align: left;
}
.retrieval__toggle:hover {
  background: rgba(176, 138, 62, 0.12);
}
.retrieval__tool {
  font-family: var(--font-mono);
  font-size: 0.66rem;
  color: var(--accent-amber);
  white-space: nowrap;
}
.retrieval__query {
  flex: 1;
  min-width: 0;
  font-size: 0.72rem;
  color: var(--parchment-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.retrieval__count {
  font-family: var(--font-mono);
  font-size: 0.64rem;
  color: var(--accent-cool);
  white-space: nowrap;
}
.retrieval__arrow {
  font-size: 0.64rem;
  color: var(--parchment-dim);
  white-space: nowrap;
}
.retrieval__hits {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 2px 0 4px;
}
.retrieval__empty {
  font-size: 0.72rem;
  font-style: italic;
  color: var(--parchment-dim);
  padding: 4px 8px;
}
.lawcard {
  padding: 7px 9px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--line-strong);
  border-left: 2px solid rgba(176, 138, 62, 0.55);
  border-radius: 3px;
}
.lawcard__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.lawcard__title {
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--parchment);
}
.lawcard__article {
  font-size: 0.7rem;
  color: var(--accent-amber);
}
.lawcard__meta {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 3px;
}
.lawcard__meta-item {
  font-family: var(--font-mono);
  font-size: 0.62rem;
  color: var(--parchment-dim);
}
.lawcard__content {
  margin: 5px 0 0;
  font-size: 0.72rem;
  line-height: 1.55;
  color: var(--parchment-muted);
}
</style>
