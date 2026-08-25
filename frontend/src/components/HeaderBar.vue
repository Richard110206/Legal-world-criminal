<script setup lang="ts">
import { computed, ref } from "vue";
import { useSession } from "../composables/useSession";
import { stageAccent } from "../lib/caseState";
import LearningDossier from "./LearningDossier.vue";

const session = useSession();

const showDossier = ref(false);

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
      <div class="hdr__caseState" :style="{ '--accent': accent }">
        <span class="hdr__caseStateKicker">CURRENT STAGE</span>
        <span class="hdr__caseStateValue">
          {{ session.state.caseState || "空闲" }}
        </span>
        <span v-if="session.state.caseId" class="hdr__caseId mono">
          case · {{ session.state.caseId }}
        </span>
      </div>
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

.hdr__caseState {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 6px 28px;
  position: relative;
}
.hdr__caseState::before,
.hdr__caseState::after {
  content: "";
  position: absolute;
  top: 50%;
  width: 16px;
  height: 1px;
  background: var(--accent);
  opacity: 0.6;
}
.hdr__caseState::before { left: 0; }
.hdr__caseState::after { right: 0; }

.hdr__caseStateKicker {
  font-family: var(--font-mono);
  font-size: 0.62rem;
  letter-spacing: 0.22em;
  color: var(--parchment-dim);
}
.hdr__caseStateValue {
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 1.4rem;
  font-weight: 700;
  color: var(--accent, var(--parchment));
  letter-spacing: 0.04em;
}
.hdr__caseId {
  font-size: 0.7rem;
  color: var(--parchment-dim);
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
