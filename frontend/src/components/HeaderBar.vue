<script setup lang="ts">
import { computed, ref } from "vue";
import { useSession } from "../composables/useSession";
import { stageAccent } from "../lib/caseState";
import LearningDossier from "./LearningDossier.vue";

const session = useSession();

const showDossier = ref(false);

const props = defineProps<{ view?: "case" | "preview" | "review" }>();
const emit = defineEmits<{ (e: "navigate", view: "case" | "preview" | "review"): void }>();

type AppView = "case" | "preview" | "review";
const navItems: { key: AppView; label: string; title: string }[] = [
  { key: "preview", label: "预习", title: "诊断测验：摸底当前知识点掌握情况" },
  { key: "case", label: "精学 · 案例教学法", title: "进入案件工作台，扮演辩护律师走完刑事流程" },
  { key: "review", label: "复习", title: "弱点补强：基于画像与答题历史的试题推送" },
];

const wsDotClass = computed(() => {
  switch (session.state.wsStatus) {
    case "open":
      return "dot--ok";
    case "connecting":
      return "dot--amber";
    case "unauthorized":
    case "error":
      return "dot--err";
    default:
      return "dot--idle";
  }
});

const wsLabel = computed(() => {
  switch (session.state.wsStatus) {
    case "open":
      return "已连接";
    case "connecting":
      return "重连中";
    case "unauthorized":
      return "鉴权失败";
    case "closed":
    case "error":
      return "已断开";
    default:
      return "未连接";
  }
});

const accent = computed(() => stageAccent(session.state.caseState));
</script>

<template>
  <header class="hdr">
    <div class="hdr__brand">
      <div class="hdr__seal"><span>法</span></div>
      <div>
        <div class="hdr__title">LegalWorld · 案例观察台</div>
        <div class="hdr__sub mono">
          {{ session.backendVersion.value ?? "—" }}
        </div>
      </div>
    </div>

    <div class="hdr__center">
      <nav class="hdr__nav" aria-label="学习模块">
        <button
          v-for="item in navItems"
          :key="item.key"
          class="hdr__navBtn"
          :class="{ 'hdr__navBtn--active': (props.view ?? 'case') === item.key }"
          :title="item.title"
          @click="emit('navigate', item.key)"
        >
          {{ item.label }}
        </button>
      </nav>
    </div>

    <div class="hdr__right">
      <div class="ws" :class="wsDotClass">
        <span class="ws__dot pulse-dot"></span>
        <span class="ws__label">{{ wsLabel }}</span>
      </div>

      <div class="hdr__controls">
        <button
          v-if="!session.state.simulationRunning"
          class="btn btn--primary"
          :disabled="!session.state.selectedCaseId"
          @click="session.startSimulation()"
        >
          开始模拟
        </button>
        <button v-else class="btn" @click="session.pauseSimulation">
          暂停
        </button>
        <button class="btn btn--ghost" @click="session.restartSimulation">
          重置
        </button>
        <button class="btn hdr__dossier" @click="showDossier = true">
          学习档案
        </button>
      </div>

      <LearningDossier v-if="showDossier" @close="showDossier = false" />

      <div class="hdr__user">
        <span class="mono">{{ session.state.email }}</span>
        <button class="btn btn--ghost" @click="session.logout">退出</button>
      </div>
    </div>
  </header>
</template>

<style scoped>
.hdr {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) auto minmax(280px, 1fr);
  align-items: center;
  gap: 24px;
  padding: 16px 28px;
  background:
    linear-gradient(180deg, var(--ink-800), var(--ink-850, var(--ink-800)));
  border-bottom: 1px solid var(--line-strong);
}

.hdr__brand {
  display: flex;
  align-items: center;
  gap: 14px;
}
.hdr__seal {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  background: var(--accent);
  display: grid;
  place-items: center;
  color: #fff;
  font-family: "Noto Serif SC", serif;
  font-weight: 900;
  font-size: 1.1rem;
  box-shadow: 0 0 0 3px var(--ink-800), 0 0 0 4px rgba(196, 71, 27, 0.4);
  transform: rotate(-4deg);
}
.hdr__title {
  font-family: "Noto Serif SC", var(--font-display);
  font-weight: 600;
  font-size: 1.05rem;
  letter-spacing: 0.02em;
}
.hdr__sub {
  font-size: 0.72rem;
  color: var(--parchment-dim);
  margin-top: 2px;
}

.hdr__center {
  text-align: center;
}

.hdr__nav {
  display: inline-flex;
  gap: 6px;
  padding: 5px;
  border: 1px solid var(--line);
  background: rgba(0, 0, 0, 0.25);
}
.hdr__navBtn {
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 0.84rem;
  letter-spacing: 0.04em;
  color: var(--parchment-muted);
  background: transparent;
  border: 1px solid transparent;
  padding: 6px 16px;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s, background 0.15s;
}
.hdr__navBtn:hover {
  color: var(--parchment);
  border-color: var(--line-strong);
}
.hdr__navBtn--active {
  color: var(--parchment);
  border-color: rgba(176, 138, 62, 0.6);
  background: rgba(176, 138, 62, 0.1);
}

.hdr__right {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
}

.ws {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border: 1px solid var(--line);
  border-radius: 2px;
  font-family: var(--font-mono);
  font-size: 0.76rem;
}
.ws__dot { color: currentColor; }
.ws--ok { color: var(--accent-success); border-color: rgba(122, 153, 98, 0.3); }
.ws--amber { color: var(--accent-amber); border-color: rgba(176, 138, 62, 0.3); }
.ws--err { color: var(--accent); border-color: rgba(196, 71, 27, 0.4); }
.ws--idle { color: var(--parchment-dim); }

.hdr__controls {
  display: flex;
  gap: 8px;
}

.hdr__dossier {
  color: var(--accent-amber);
  border-color: rgba(176, 138, 62, 0.5);
}
.hdr__dossier:hover {
  border-color: var(--accent-amber);
  background: rgba(176, 138, 62, 0.08);
}

.hdr__user {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-left: 12px;
  border-left: 1px solid var(--line);
}
.hdr__user .mono {
  font-size: 0.78rem;
  color: var(--parchment-muted);
}

@media (max-width: 1180px) {
  .hdr {
    grid-template-columns: 1fr auto;
  }
  .hdr__center {
    display: none;
  }
}
</style>
