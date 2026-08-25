<script setup lang="ts">
import { computed } from "vue";
import { useSession } from "../composables/useSession";
import { displayState } from "../lib/caseState";
import receptionImg from "../assets/stages/reception.png";
import investigationImg from "../assets/stages/investigation.png";
import prosecutionImg from "../assets/stages/prosecution.png";
import defenseImg from "../assets/stages/defense.png";
import trialImg from "../assets/stages/trial.png";
import appealImg from "../assets/stages/appeal.png";

const session = useSession();

/**
 * 阶段 → 背景图映射（按案件状态机）。
 * 后续新增阶段图：把图片放进 src/assets/stages/，
 * 在此 import 并加入 STAGE_BACKDROPS 即可。
 */
const STAGE_BACKDROPS: { match: (state: string) => boolean; src: string }[] = [
  // 刑事：侦查 / 审查起诉 / 辩护词 / 一审 / 二审
  { match: (s) => s === "侦查阶段", src: investigationImg },
  { match: (s) => s === "审查起诉阶段", src: prosecutionImg },
  { match: (s) => s.includes("辩护词"), src: defenseImg },
  { match: (s) => s.includes("刑事一审"), src: trialImg },
  { match: (s) => s.includes("刑事二审") || s.includes("刑事终审"), src: appealImg },
  // 兜底：接待/委托洽谈/空闲等全流程入口
  { match: () => true, src: receptionImg },
];

const backdrop = computed(() => {
  const state = session.state.caseOverallState || session.state.caseState || "";
  return STAGE_BACKDROPS.find((b) => b.match(state))?.src ?? receptionImg;
});

const stageLabel = computed(() => {
  const raw = session.state.caseOverallState || session.state.caseState || "";
  return displayState(raw, session.state.caseCategory) || "空闲";
});
</script>

<template>
  <div class="backdrop">
    <Transition name="backdrop-fade" mode="out-in">
      <div
        :key="backdrop"
        class="backdrop__img"
        :style="{ backgroundImage: `url(${backdrop})` }"
      ></div>
    </Transition>
    <!-- 轻遮罩：整屏保氛围，底部压暗衬托悬浮对话框 -->
    <div class="backdrop__scrim"></div>
    <div class="backdrop__caption">
      <span class="backdrop__kicker">CURRENT STAGE</span>
      <span class="backdrop__stage">{{ stageLabel }}</span>
    </div>
  </div>
</template>

<style scoped>
.backdrop {
  position: absolute;
  inset: 0;
  overflow: hidden;
}

.backdrop__img {
  position: absolute;
  inset: 0;
  background-size: cover;
  background-position: center;
  transform: scale(1.02);
  filter: saturate(0.96);
}

.backdrop__scrim {
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, rgba(14, 12, 8, 0.1), rgba(14, 12, 8, 0.02));
}

.backdrop__caption {
  position: absolute;
  left: 36px;
  top: 24px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  pointer-events: none;
  text-shadow: 0 2px 14px rgba(0, 0, 0, 0.9);
}

.backdrop__kicker {
  font-family: var(--font-mono);
  font-size: 0.66rem;
  letter-spacing: 0.24em;
  color: var(--accent-amber);
}

.backdrop__stage {
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 2rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: #f5efe2;
}

.backdrop-fade-enter-active,
.backdrop-fade-leave-active {
  transition: opacity 1.2s ease;
}
.backdrop-fade-enter-from,
.backdrop-fade-leave-to {
  opacity: 0;
}
</style>
