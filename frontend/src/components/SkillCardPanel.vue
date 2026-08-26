<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api } from "../lib/api";
import type { SkillCardDetail, SkillCardSummary } from "../lib/types";

const MAX_SELECTED = 3;

const cards = ref<SkillCardSummary[]>([]);
const loaded = ref(false);
const open = ref(false);
const expandedSlug = ref<string | null>(null);
const detailCache = ref<Record<string, SkillCardDetail>>({});
const selected = ref<string[]>([]);
const studentId = ref<string>("");

const selectedCards = computed(() =>
  cards.value.filter((c) => selected.value.includes(c.slug)),
);

/** 给 submit 用的注入文本：勾选卡片的知识点+要点，拼成简短提示块 */
const injectionText = computed(() => {
  if (!selectedCards.value.length) return "";
  const lines = selectedCards.value.map((c) => `· ${c.knowledge_point}：${c.description}`);
  return `【我的技能卡提醒】\n${lines.join("\n")}\n（办理本案时注意以上补弱要点）`;
});

function toggle(slug: string) {
  const idx = selected.value.indexOf(slug);
  if (idx >= 0) {
    selected.value.splice(idx, 1);
    return;
  }
  if (selected.value.length >= MAX_SELECTED) return;
  selected.value.push(slug);
}

async function toggleExpand(slug: string) {
  if (expandedSlug.value === slug) {
    expandedSlug.value = null;
    return;
  }
  expandedSlug.value = slug;
  if (!detailCache.value[slug]) {
    try {
      detailCache.value[slug] = await api.skillCardDetail(studentId.value, slug);
    } catch {
      detailCache.value[slug] = {
        slug,
        name: slug,
        description: "",
        knowledge_point: slug,
        stage: "",
        charge: "",
        review_count: 1,
        status_history: [],
        updated_at: "",
        content: "（读取失败）",
      };
    }
  }
}

onMounted(async () => {
  try {
    const me = await api.me();
    studentId.value = me.id;
    cards.value = await api.skillCards(me.id);
  } catch {
    cards.value = [];
  } finally {
    loaded.value = true;
  }
});

defineExpose({ injectionText });
</script>

<template>
  <div v-if="loaded && cards.length" class="skillcards">
    <button class="skillcards__toggle" type="button" @click="open = !open">
      <span class="skillcards__label">
        我的技能卡
        <span class="skillcards__count">{{ cards.length }}</span>
      </span>
      <span v-if="selected.length" class="skillcards__picked">
        已选 {{ selected.length }}/3
      </span>
      <span class="skillcards__arrow" :class="{ 'is-open': open }">▾</span>
    </button>

    <div v-if="open" class="skillcards__body">
      <p class="skillcards__hint">
        上局批阅沉淀的补弱卡。勾选 1-3 张，提交发言时会附上提醒。
      </p>
      <ul class="skillcards__list">
        <li v-for="card in cards" :key="card.slug" class="skillcard">
          <label class="skillcard__row">
            <input
              type="checkbox"
              :checked="selected.includes(card.slug)"
              :disabled="!selected.includes(card.slug) && selected.length >= MAX_SELECTED"
              @change="toggle(card.slug)"
            />
            <span class="skillcard__kp">{{ card.knowledge_point }}</span>
            <span v-if="card.stage" class="skillcard__stage mono">{{ card.stage }}</span>
            <span v-if="card.review_count > 1" class="skillcard__count" title="复训次数">
              ×{{ card.review_count }}
            </span>
            <button
              class="skillcard__expand link"
              type="button"
              @click.prevent="toggleExpand(card.slug)"
            >
              {{ expandedSlug === card.slug ? "收起" : "查看" }}
            </button>
          </label>
          <div v-if="expandedSlug === card.slug" class="skillcard__detail">
            <pre class="skillcard__content">{{ detailCache[card.slug]?.content ?? "加载中…" }}</pre>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.skillcards {
  flex-shrink: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.02);
  margin-top: 4px;
  overflow: hidden;
}

.skillcards__toggle {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  background: none;
  border: none;
  color: var(--parchment-muted);
  cursor: pointer;
  font-size: 0.82rem;
}
.skillcards__toggle:hover {
  color: var(--parchment);
}

.skillcards__label {
  font-family: "Noto Serif SC", var(--font-display);
  font-weight: 600;
  color: var(--parchment);
}

.skillcards__count {
  display: inline-block;
  min-width: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: var(--accent);
  color: #fff;
  font-family: var(--font-mono);
  font-size: 0.68rem;
  text-align: center;
  line-height: 1.5;
}

.skillcards__picked {
  font-family: var(--font-mono);
  font-size: 0.68rem;
  color: var(--accent);
}

.skillcards__arrow {
  margin-left: auto;
  transition: transform 0.2s;
  font-size: 0.7rem;
}
.skillcards__arrow.is-open {
  transform: rotate(180deg);
}

.skillcards__body {
  padding: 0 14px 10px;
  border-top: 1px dashed var(--line);
}

.skillcards__hint {
  margin: 8px 0;
  font-size: 0.72rem;
  color: var(--parchment-muted);
}

.skillcards__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.skillcard__row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.8rem;
}
.skillcard__row:hover {
  background: rgba(255, 255, 255, 0.04);
}

.skillcard__kp {
  color: var(--parchment);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skillcard__stage {
  flex-shrink: 0;
  font-size: 0.64rem;
  color: var(--parchment-muted);
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 0 4px;
}

.skillcard__count {
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: 0.66rem;
  color: var(--accent);
}

.skillcard__expand {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 0.7rem;
}

.skillcard__detail {
  margin: 2px 0 6px 26px;
  max-height: 220px;
  overflow-y: auto;
}

.skillcard__content {
  margin: 0;
  padding: 8px 10px;
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid var(--line);
  border-radius: 6px;
  font-family: inherit;
  font-size: 0.74rem;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--parchment-muted);
}
</style>
