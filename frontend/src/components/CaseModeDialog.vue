<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { CasePickerEntry, PlayerMode } from "../lib/types";

const props = defineProps<{
  open: boolean;
  caseEntry: CasePickerEntry | null;
}>();

const emit = defineEmits<{
  close: [];
  confirm: [mode: PlayerMode, caseId: string];
}>();

const selected = ref<PlayerMode | null>(null);

watch(
  () => props.open,
  (open) => {
    if (open) selected.value = null;
  },
);

function confirm() {
  if (!selected.value || !props.caseEntry) return;
  emit("confirm", selected.value, props.caseEntry.case_id);
}


const modeOptions = computed<
  {
    value: PlayerMode;
    kicker: string;
    title: string;
    desc: string;
    icon: string;
  }[]
>(() => [
  {
    value: "auto",
    kicker: "OBSERVER MODE",
    title: "自动模拟",
    desc: "AI 扮演所有角色(辩护律师、检察官、法官、当事人)。你作为观察者旁观整个刑事诉讼生命周期,适合熟悉案件脉络与法律推理。",
    icon: "◎",
  },
  {
    value: "player",
    kicker: "PLAYER MODE",
    title: "扮演辩护律师",
    desc: "你将接手辩护律师席位,在侦查会见、审查起诉、辩护词、一审庭审、(上诉)每个节点亲自发言。AI 扮演检察官、法官、被告人及家属,实时回应你。",
    icon: "§",
  },
]);
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="open" class="overlay" @click.self="emit('close')">
        <div class="dialog reveal">
          <header class="dialog__head">
            <p class="kicker">SELECT PARTICIPATION MODE · 选择参与模式</p>
            <h2 v-if="caseEntry" class="dialog__title">
              {{ caseEntry.title }}
            </h2>
            <p v-if="caseEntry" class="dialog__sub muted">
              {{ caseEntry.raw_case_cause || "—" }}
              <span class="mono dim"> · {{ caseEntry.case_id }}</span>
            </p>
            <button class="dialog__close" @click="emit('close')">×</button>
          </header>

          <div class="rule"></div>

          <div class="modes">
            <button
              v-for="opt in modeOptions"
              :key="opt.value"
              type="button"
              class="mode"
              :class="{ 'mode--active': selected === opt.value }"
              @click="selected = opt.value"
            >
              <div class="mode__icon">{{ opt.icon }}</div>
              <div class="mode__body">
                <p class="mode__kicker">{{ opt.kicker }}</p>
                <p class="mode__title">{{ opt.title }}</p>
                <p class="mode__desc muted">{{ opt.desc }}</p>
              </div>
              <div class="mode__check" aria-hidden="true">
                <span></span>
              </div>
            </button>
          </div>

          <footer class="dialog__foot">
            <span class="hint mono dim" v-if="!selected">
              — 请选择一种模式 —
            </span>
            <span class="hint mono" v-else>
              已选:
              {{
                selected === "auto"
                  ? "自动模拟"
                  : "扮演辩护律师"
              }}
            </span>
            <div class="spacer"></div>
            <button class="btn btn--ghost" @click="emit('close')">取消</button>
            <button
              class="btn btn--primary"
              :disabled="!selected"
              @click="confirm"
            >
              开始 →
            </button>
          </footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  background: rgba(8, 6, 4, 0.72);
  backdrop-filter: blur(6px);
  display: grid;
  place-items: center;
  padding: 32px 20px;
}

.dialog {
  position: relative;
  width: 100%;
  max-width: 720px;
  max-height: 90vh;
  overflow-y: auto;
  padding: 32px 36px 24px;
  background: linear-gradient(180deg, var(--ink-750), var(--ink-800));
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  box-shadow:
    0 1px 0 rgba(255, 240, 210, 0.04) inset,
    0 60px 120px -50px rgba(0, 0, 0, 0.9);
}
.dialog::before,
.dialog::after {
  content: "";
  position: absolute;
  width: 14px;
  height: 14px;
  border: 1px solid var(--accent);
}
.dialog::before {
  top: 10px;
  left: 10px;
  border-right: none;
  border-bottom: none;
}
.dialog::after {
  bottom: 10px;
  right: 10px;
  border-left: none;
  border-top: none;
}

.dialog__head {
  position: relative;
  padding-right: 36px;
}

.kicker {
  margin: 0 0 8px;
  font-family: var(--font-mono);
  font-size: 0.7rem;
  letter-spacing: 0.22em;
  color: var(--accent);
}

.dialog__title {
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 1.6rem;
  font-weight: 700;
  margin: 0;
  letter-spacing: 0.02em;
}

.dialog__sub {
  margin: 6px 0 0;
  font-size: 0.88rem;
}

.dialog__close {
  position: absolute;
  top: -8px;
  right: -8px;
  width: 30px;
  height: 30px;
  background: var(--ink-900);
  border: 1px solid var(--line-strong);
  color: var(--parchment-muted);
  border-radius: 50%;
  font-size: 1.2rem;
  line-height: 1;
  cursor: pointer;
  transition: all 0.15s ease;
}
.dialog__close:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.modes {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  margin: 20px 0;
}

.mode {
  position: relative;
  display: grid;
  grid-template-columns: 48px 1fr 24px;
  gap: 14px;
  align-items: flex-start;
  padding: 18px;
  text-align: left;
  background: rgba(0, 0, 0, 0.2);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  cursor: pointer;
  transition: all 0.18s ease;
  font-family: inherit;
  color: inherit;
}
.mode:hover {
  border-color: var(--line-strong);
  background: rgba(0, 0, 0, 0.3);
}
.mode--active {
  border-color: var(--accent);
  background: linear-gradient(
    180deg,
    rgba(196, 71, 27, 0.1),
    rgba(0, 0, 0, 0.25)
  );
  box-shadow: 0 0 0 1px var(--accent) inset;
}

.mode__icon {
  width: 48px;
  height: 48px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--ink-900);
  border: 1px solid var(--line-strong);
  color: var(--accent);
  font-family: var(--font-display);
  font-size: 1.5rem;
  font-weight: 700;
}
.mode--active .mode__icon {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}

.mode__kicker {
  margin: 0 0 4px;
  font-family: var(--font-mono);
  font-size: 0.62rem;
  letter-spacing: 0.2em;
  color: var(--parchment-dim);
}
.mode--active .mode__kicker {
  color: var(--accent);
}

.mode__title {
  margin: 0 0 8px;
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 1.15rem;
  font-weight: 600;
  color: var(--parchment);
}

.mode__desc {
  margin: 0;
  font-size: 0.82rem;
  line-height: 1.55;
}

.mode__check {
  margin-top: 4px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1px solid var(--line-strong);
  display: grid;
  place-items: center;
}
.mode__check span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: transparent;
  transition: background 0.2s ease;
}
.mode--active .mode__check {
  border-color: var(--accent);
}
.mode--active .mode__check span {
  background: var(--accent);
}

.dialog__foot {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-top: 18px;
  border-top: 1px dashed var(--line);
}
.dialog__foot .hint {
  font-size: 0.78rem;
  color: var(--parchment-muted);
}
.spacer {
  flex: 1;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.18s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 640px) {
  .modes {
    grid-template-columns: 1fr;
  }
}
</style>
