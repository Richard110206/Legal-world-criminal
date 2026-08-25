<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useSession } from "../composables/useSession";
import { renderMarkdown } from "../lib/markdown";
import CasePicker from "./CasePicker.vue";
import PlayerInputPanel from "./PlayerInputPanel.vue";

const session = useSession();

const entries = computed(() => session.dialogue.value);
const feedEl = ref<HTMLElement | null>(null);

const showPicker = computed(
  () => entries.value.length === 0 && !session.state.simulationRunning,
);

const modeLabel = computed(() =>
  session.state.playerMode === "player" ? "扮演辩护律师" : "自动模拟",
);

const waitingGate = computed(() => session.state.waitingGate);
const playerPanelVisible = computed(() => session.pendingPlayerRequest.value !== null);

/* ── 即时法条校验警示 ── */
const citationNotice = computed(() => session.state.citationNotice);
const citationExpanded = ref(false);
watch(citationNotice, (n) => {
  citationExpanded.value = false;
  if (n) {
    void nextTick(() => {
      if (feedEl.value) feedEl.value.scrollTop = feedEl.value.scrollHeight;
    });
  }
});

function detailContent(d: Record<string, unknown>): string {
  return String(d?.content ?? "");
}

function detailSuggestion(d: Record<string, unknown>): string {
  return String(d?.suggestion ?? d?.message ?? "");
}

/** 新对话到来时自动滚动到底部 */
watch(
  () => entries.value.length,
  async () => {
    if (!playerPanelVisible.value && feedEl.value) {
      await nextTick();
      feedEl.value.scrollTop = feedEl.value.scrollHeight;
    }
  },
);

/** 玩家模式下等待继续：点击页面任意处（输入面板/选案器除外）进入下一段对话 */
function handlePageClick(evt: MouseEvent) {
  if (session.state.playerMode !== "player") return;
  if (!waitingGate.value) return;
  const target = evt.target as HTMLElement | null;
  if (target?.closest(".panel") || target?.closest(".picker")) return;
  session.continueDialogue();
}

onMounted(() => {
  window.addEventListener("click", handlePageClick);
});
onBeforeUnmount(() => {
  window.removeEventListener("click", handlePageClick);
});
</script>

<template>
  <div class="vn-layer">
    <!-- 选案视图 -->
    <div v-if="showPicker" class="vn-picker">
      <CasePicker />
    </div>

    <template v-else>
      <div class="vn-box">
        <!-- 对话历史：多行滚动展示；等待玩家输入时压缩为名牌行，把空间让给输入面板 -->
        <div class="turn" :class="{ 'turn--compact': playerPanelVisible }">
          <div class="turn__feed" ref="feedEl">
            <article
              v-for="(entry, idx) in entries"
              :key="entry.id"
              class="msg"
              :class="{
                'msg--player': entry.player_responsibility,
                'msg--latest': idx === entries.length - 1,
              }"
            >
              <div class="msg__nameplate">
                <span class="msg__speaker">{{ entry.speaker_name }}</span>
                <span v-if="entry.scenario_type" class="msg__scn mono">
                  {{ entry.scenario_type }}
                </span>
              </div>
              <div class="msg__content md" v-html="renderMarkdown(entry.content)"></div>
            </article>

            <!-- 即时法条校验警示：挂在最新发言之后 -->
            <aside
              v-if="citationNotice"
              class="cit"
              :class="{ 'cit--expanded': citationExpanded, 'cit--ok': citationNotice.status === 'ok' }"
            >
              <button class="cit__head" @click="citationExpanded = !citationExpanded">
                <span class="cit__seal">§</span>
                <span class="cit__title">
                  {{ citationNotice.status === "ok"
                    ? "法条引用核验 · 全部通过"
                    : `法条引用核验 · ${citationNotice.messages.length} 处待核对` }}
                </span>
                <span class="cit__caret">{{ citationExpanded ? "收起" : "展开条文" }}</span>
              </button>
              <ul class="cit__list">
                <li
                  v-for="(m, i) in citationNotice.messages"
                  :key="i"
                  class="cit__msg"
                  :class="{ 'cit__msg--ok': citationNotice.status === 'ok' }"
                >
                  {{ citationNotice.status === "ok" ? "✓" : "⚠" }} {{ m }}
                </li>
              </ul>
              <div v-if="citationExpanded" class="cit__details">
                <div
                  v-for="(d, i) in citationNotice.details"
                  :key="i"
                  class="cit__detail"
                >
                  <p class="cit__cite mono">{{ d?.citation ?? "" }}</p>
                  <p v-if="detailSuggestion(d)" class="cit__suggestion">
                    {{ detailSuggestion(d) }}
                  </p>
                  <blockquote v-if="detailContent(d)" class="cit__law">
                    {{ detailContent(d) }}
                  </blockquote>
                </div>
                <div v-if="!citationNotice.details.length" class="cit__dim">
                  无可展开的条文内容
                </div>
              </div>
              <button class="cit__dismiss" @click="session.dismissCitationNotice()">
                知道了
              </button>
            </aside>

            <div v-if="!entries.length" class="vn-box__empty">
              模拟已启动，后端将通过 WebSocket 实时推送每一轮发言。
            </div>
          </div>
          <footer v-if="!playerPanelVisible" class="turn__foot">
            <span
              v-if="waitingGate && session.state.playerMode === 'player' && !playerPanelVisible"
              class="turn__hint"
            >
              ▸ 点击页面任意处继续
              <span class="turn__hintDim" v-if="waitingGate.speakerName">
                · 下一位发言人：{{ waitingGate.speakerName }}
              </span>
            </span>
            <span v-else-if="playerPanelVisible" class="turn__hint turn__hintInput">
              ⏸ 等待你输入发言——提交后将自动继续
            </span>
            <span v-else class="turn__hint turn__hintDim">
              对话进行中… · {{ entries.length }} 轮
            </span>
          </footer>
        </div>

        <PlayerInputPanel />
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 对话层：填充图片下方的独立条区，不遮挡图片 */
.vn-layer {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

/* 选案视图 */
.vn-picker {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 20px 32px;
  overflow: hidden;
}

/* 对话框：横向全宽，多行对话历史 */
.vn-box {
  width: 100%;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 8px 28px 8px;
  overflow: hidden;
}

.turn {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* 等待玩家输入：压缩为名牌提示行，剩余空间全部让给输入面板 */
.turn--compact {
  flex: 0 0 auto;
  overflow: hidden;
}
.turn--compact .turn__feed {
  display: none;
}

/* 对话历史滚动区 */
.turn__feed {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px 4px 8px;
  scroll-behavior: smooth;
}

/* 单条消息 */
.msg {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  animation: ink-rise 0.35s ease both;
}

.msg__nameplate {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 3px;
}

.msg__speaker {
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 0.98rem;
  font-weight: 700;
  color: var(--parchment);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.msg--player .msg__speaker {
  color: var(--accent);
}

.msg__scn {
  font-size: 0.6rem;
  padding: 1px 5px;
  border: 1px solid var(--line);
  border-radius: 2px;
  color: var(--accent-amber);
}

.msg__content {
  margin: 0;
  font-family: var(--font-body);
  font-size: 1.0rem;
  line-height: 1.7;
  color: var(--parchment);
  word-break: break-word;
  font-variation-settings: "opsz" 18;
}
.msg--player .msg__content {
  border-left: 2px solid var(--accent);
  padding-left: 12px;
}
.msg--latest .msg__content {
  border-left: 2px solid rgba(196, 71, 27, 0.45);
  padding-left: 12px;
}

/* Markdown 元素样式 */
.msg__content.md :deep(p) { margin: 0 0 0.5em; }
.msg__content.md :deep(p:last-child) { margin-bottom: 0; }
.msg__content.md :deep(strong) {
  color: #f5efe2;
  font-weight: 700;
}
.msg__content.md :deep(em) { color: var(--accent-amber); }
.msg__content.md :deep(ul),
.msg__content.md :deep(ol) {
  margin: 0.4em 0;
  padding-left: 1.4em;
}
.msg__content.md :deep(li) { margin: 0.15em 0; }
.msg__content.md :deep(blockquote) {
  margin: 0.5em 0;
  padding: 0.2em 0.9em;
  border-left: 2px solid var(--accent-muted);
  color: var(--parchment-muted);
}
.msg__content.md :deep(h1),
.msg__content.md :deep(h2),
.msg__content.md :deep(h3),
.msg__content.md :deep(h4) {
  font-family: "Noto Serif SC", var(--font-display);
  margin: 0.6em 0 0.3em;
  color: #f5efe2;
}
.msg__content.md :deep(h1) { font-size: 1.25rem; }
.msg__content.md :deep(h2) { font-size: 1.15rem; }
.msg__content.md :deep(h3),
.msg__content.md :deep(h4) { font-size: 1.05rem; }
.msg__content.md :deep(code) {
  font-family: var(--font-mono);
  font-size: 0.88em;
  padding: 0.1em 0.35em;
  background: rgba(0, 0, 0, 0.35);
  border-radius: 2px;
}
.msg__content.md :deep(hr) {
  border: none;
  border-top: 1px dashed var(--line-strong);
  margin: 0.8em 0;
}

.turn__foot {
  flex-shrink: 0;
  padding-top: 6px;
  min-height: 20px;
}

.turn__hint {
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.82rem;
  color: var(--accent);
  animation: hint-pulse 2.2s ease-in-out infinite;
}
.turn__hintDim {
  color: var(--parchment-dim);
  animation: none;
}
.turn__hintInput {
  color: var(--accent-amber);
}

/* ── 即时法条校验警示卡（暗琥珀 · 档案批注风） ── */
.cit {
  flex-shrink: 0;
  margin: 4px 0 2px;
  border: 1px solid rgba(176, 138, 62, 0.55);
  border-left: 3px solid var(--accent-amber);
  border-radius: 3px;
  background:
    linear-gradient(180deg, rgba(176, 138, 62, 0.10), rgba(176, 138, 62, 0.04)),
    var(--ink-800);
  animation: ink-rise 0.4s ease both;
  overflow: hidden;
}
.cit__head {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--accent-amber);
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.82rem;
  text-align: left;
}
.cit__head:hover { background: rgba(176, 138, 62, 0.08); }
.cit__seal {
  width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(176, 138, 62, 0.6);
  border-radius: 2px;
  font-family: var(--font-display);
  font-size: 0.78rem;
  flex-shrink: 0;
  transform: rotate(-3deg);
}
.cit__title {
  flex: 1;
  font-weight: 600;
  letter-spacing: 0.02em;
}
.cit__caret {
  font-size: 0.7rem;
  color: var(--parchment-dim);
  border-bottom: 1px dashed rgba(176, 138, 62, 0.5);
  padding-bottom: 1px;
  flex-shrink: 0;
}
.cit__list {
  margin: 0;
  padding: 0 12px 6px 36px;
  list-style: none;
}
.cit__msg {
  font-size: 0.82rem;
  line-height: 1.55;
  color: #d8bd85;
  margin: 2px 0;
}
.cit--ok {
  border-color: rgba(96, 148, 110, 0.55);
  border-left-color: #6a9a76;
  background:
    linear-gradient(180deg, rgba(96, 148, 110, 0.10), rgba(96, 148, 110, 0.04)),
    var(--ink-800);
}
.cit__msg--ok {
  color: #9ec7a8;
}
.cit__details {
  padding: 8px 12px 8px;
  border-top: 1px dashed rgba(176, 138, 62, 0.3);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.cit__detail { animation: ink-rise 0.3s ease both; }
.cit__cite {
  margin: 0 0 3px;
  font-size: 0.76rem;
  color: var(--accent-amber);
  letter-spacing: 0.04em;
}
.cit__suggestion {
  margin: 0 0 4px;
  font-size: 0.8rem;
  color: var(--parchment-muted);
}
.cit__law {
  margin: 0;
  padding: 6px 10px;
  background: rgba(0, 0, 0, 0.3);
  border-left: 2px solid rgba(176, 138, 62, 0.5);
  border-radius: 2px;
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.84rem;
  line-height: 1.7;
  color: var(--parchment);
}
.cit__dim {
  font-size: 0.78rem;
  color: var(--parchment-faint);
  font-style: italic;
}
.cit__dismiss {
  display: block;
  width: 100%;
  padding: 5px 0 6px;
  background: transparent;
  border: none;
  border-top: 1px dashed rgba(176, 138, 62, 0.3);
  color: var(--parchment-dim);
  font-family: "Noto Serif SC", var(--font-body);
  font-size: 0.76rem;
  cursor: pointer;
}
.cit__dismiss:hover { color: var(--accent-amber); }

.vn-box__empty {
  color: var(--parchment-muted);
  font-size: 0.9rem;
  font-style: italic;
  text-align: center;
  margin: auto 0;
}

@keyframes hint-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}

@media (max-width: 900px) {
  .msg__speaker { font-size: 0.9rem; }
}
</style>
