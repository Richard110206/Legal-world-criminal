<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useSession } from "../composables/useSession";
import { api } from "../lib/api";
import SkillCardPanel from "./SkillCardPanel.vue";

const session = useSession();

const draft = ref("");
const polished = ref<string | null>(null);
const submittedOriginal = ref<string>("");
const busy = ref<"idle" | "drafting" | "polishing" | "submitting">("idle");
const error = ref<string | null>(null);
const skillCardPanel = ref<InstanceType<typeof SkillCardPanel> | null>(null);

const request = computed(() => session.pendingPlayerRequest.value);
const visible = computed(() => request.value !== null);

watch(
  () => request.value?.request_id,
  (id) => {
    if (id) {
      draft.value = "";
      polished.value = null;
      submittedOriginal.value = "";
      error.value = null;
      busy.value = "idle";
    }
  },
);

const finalMessage = computed(() => {
  if (polished.value && polished.value.trim()) return polished.value;
  return draft.value;
});

const canSubmit = computed(
  () => !!request.value && finalMessage.value.trim().length > 0 && busy.value === "idle",
);

async function handleDraft() {
  if (!request.value) return;
  busy.value = "drafting";
  error.value = null;
  try {
    const res = await api.playerDraft(request.value.request_id);
    const text = res.assist?.ai_polished_message ?? "";
    if (res.success && text) {
      polished.value = null;
      draft.value = text;
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    busy.value = "idle";
  }
}

async function handlePolish() {
  if (!request.value || !draft.value.trim()) return;
  busy.value = "polishing";
  error.value = null;
  submittedOriginal.value = draft.value;
  try {
    const res = await api.playerPolish(request.value.request_id, draft.value);
    const text = res.assist?.ai_polished_message ?? "";
    if (res.success && text) {
      polished.value = text;
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    busy.value = "idle";
  }
}

function revertPolish() {
  polished.value = null;
}

async function handleSubmit() {
  if (!request.value || !canSubmit.value) return;
  busy.value = "submitting";
  error.value = null;
  const skillInjection = skillCardPanel.value?.injectionText || "";
  const message = skillInjection
    ? `${finalMessage.value}\n\n${skillInjection}`
    : finalMessage.value;
  try {
    const res = await api.playerRespond(request.value.request_id, message, {
      original_message: submittedOriginal.value || undefined,
      polished_message: polished.value || undefined,
      used_ai_polish: !!polished.value,
    });
    const fb = res?.citation_feedback;
    if (fb && Array.isArray(fb.messages) && fb.messages.length > 0) {
      session.pushCitationNotice({
        id: `${Date.now()}`,
        status: fb.status,
        messages: fb.messages,
        details: Array.isArray(fb.details) ? fb.details : [],
      });
    } else {
      session.pushCitationNotice(null);
    }
    session.clearPendingPlayerRequest();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    busy.value = "idle";
  }
}
</script>

<template>
  <Transition name="slide-up">
    <section v-if="visible && request" class="panel">
      <header class="panel__head">
        <div>
          <p class="kicker">
            <span class="pulse-dot dot"></span> AWAITING YOUR INPUT
          </p>
          <h3 class="panel__title">
            轮到你了 ·
            <span class="dim">{{
              request.speaker_label || "辩护律师"
            }}</span>
          </h3>
          <p class="panel__stage mono" v-if="request.stage">
            stage · {{ request.stage }}
          </p>
        </div>
        <span class="tag tag--accent">PLAYER</span>
      </header>

      <div v-if="request.context_summary" class="panel__ctx">
        <p class="ctx__label mono">CONTEXT</p>
        <p class="ctx__body">{{ request.context_summary }}</p>
      </div>

      <p v-if="request.prompt" class="panel__prompt">{{ request.prompt }}</p>

      <SkillCardPanel ref="skillCardPanel" />

      <div class="panel__editor">
        <div class="editor__toolbar" v-if="polished">
          <span class="tag tag--amber">AI 润色版</span>
          <button class="link" @click="revertPolish">还原原稿</button>
        </div>
        <textarea
          v-model="draft"
          :disabled="!!polished || busy !== 'idle'"
          class="editor__area"
          rows="6"
          placeholder="在此输入你的发言……"
        ></textarea>
        <textarea
          v-if="polished"
          v-model="polished"
          class="editor__area editor__area--polished"
          rows="6"
        ></textarea>
      </div>

      <p v-if="error" class="panel__error">{{ error }}</p>

      <footer class="panel__foot">
        <button
          class="btn btn--ghost"
          :disabled="busy !== 'idle'"
          @click="handleDraft"
        >
          <span v-if="busy === 'drafting'">AI 起草中…</span>
          <span v-else>AI 起草</span>
        </button>
        <button
          class="btn btn--ghost"
          :disabled="busy !== 'idle' || !draft.trim() || !!polished"
          @click="handlePolish"
        >
          <span v-if="busy === 'polishing'">润色中…</span>
          <span v-else>AI 润色</span>
        </button>
        <div class="spacer"></div>
        <button
          class="btn btn--primary"
          :disabled="!canSubmit"
          @click="handleSubmit"
        >
          <span v-if="busy === 'submitting'">提交中…</span>
          <span v-else>提交发言 →</span>
        </button>
      </footer>
    </section>
  </Transition>
</template>

<style scoped>
.panel {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, var(--ink-700), var(--ink-800));
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  padding: 10px 18px 8px;
  margin-top: 4px;
  overflow-y: auto;
  box-shadow:
    0 0 0 1px rgba(196, 71, 27, 0.2),
    0 -20px 50px -30px rgba(196, 71, 27, 0.6);
}

.panel__head,
.panel__ctx,
.panel__prompt,
.panel__error {
  flex-shrink: 0;
}

.panel__head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 8px;
  padding-bottom: 8px;
  border-bottom: 1px dashed var(--line-strong);
}

.kicker {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 6px;
  font-family: var(--font-mono);
  font-size: 0.66rem;
  letter-spacing: 0.2em;
  color: var(--accent);
}
.kicker .dot {
  background: var(--accent);
  color: var(--accent);
}

.panel__title {
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 1.15rem;
  font-weight: 600;
  margin: 0;
}
.panel__title .dim {
  color: var(--parchment-muted);
  font-weight: 400;
}

.panel__stage {
  margin: 4px 0 0;
  font-size: 0.72rem;
  color: var(--parchment-dim);
}

.panel__ctx {
  margin: 0 0 8px;
  padding: 8px 12px;
  background: rgba(0, 0, 0, 0.25);
  border-left: 2px solid var(--accent-amber);
  border-radius: 2px;
}
.ctx__label {
  margin: 0 0 4px;
  font-size: 0.62rem;
  letter-spacing: 0.18em;
  color: var(--accent-amber);
}
.ctx__body {
  margin: 0;
  font-size: 0.84rem;
  line-height: 1.5;
  color: var(--parchment-muted);
  font-style: italic;
}

.panel__prompt {
  margin: 0 0 8px;
  font-family: var(--font-body);
  font-size: 0.92rem;
  line-height: 1.6;
  color: var(--parchment);
}

.panel__editor {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 8px;
}

.editor__toolbar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
}

.editor__area {
  width: 100%;
  flex: 1;
  min-height: 0;
  padding: 8px 12px;
  background: var(--ink-900);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  color: var(--parchment);
  font-family: var(--font-body);
  font-size: 0.94rem;
  line-height: 1.6;
  resize: none;
  transition: border-color 0.15s ease;
}
.editor__area:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(196, 71, 27, 0.15);
}
.editor__area::placeholder {
  color: var(--parchment-faint);
  font-style: italic;
}
.editor__area:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.editor__area--polished {
  flex: 1;
  border-color: rgba(176, 138, 62, 0.5);
  background: rgba(176, 138, 62, 0.04);
}

.link {
  background: transparent;
  border: none;
  color: var(--accent);
  font-family: var(--font-body);
  font-size: 0.82rem;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 3px;
}

.panel__error {
  margin: 0 0 10px;
  padding: 8px 12px;
  background: rgba(168, 52, 31, 0.12);
  border-left: 2px solid var(--accent);
  color: #f0b6a6;
  font-size: 0.84rem;
}

.panel__foot {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding-top: 2px;
}
.spacer {
  flex: 1;
}

.slide-up-enter-active,
.slide-up-leave-active {
  transition: all 0.3s cubic-bezier(0.2, 0.6, 0.2, 1);
}
.slide-up-enter-from,
.slide-up-leave-to {
  opacity: 0;
  transform: translateY(20px);
}
</style>
