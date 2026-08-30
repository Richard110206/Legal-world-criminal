<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useSession } from "./composables/useSession";
import AuthPanel from "./components/AuthPanel.vue";
import HeaderBar from "./components/HeaderBar.vue";
import StageRail from "./components/StageRail.vue";
import ToolPanel from "./components/ToolPanel.vue";
import DialogueFeed from "./components/DialogueFeed.vue";
import StageBackdrop from "./components/StageBackdrop.vue";
import AdaptiveQuiz from "./components/AdaptiveQuiz.vue";

const session = useSession();
onMounted(() => {
  void session.init();
});

const isLoggedIn = computed(() => session.state.email !== null);

type AppView = "case" | "preview" | "review";
const activeView = ref<AppView>("case");

function navigate(view: AppView): void {
  activeView.value = view;
}
</script>

<template>
  <div class="app-shell">
    <template v-if="isLoggedIn">
      <HeaderBar :view="activeView" @navigate="navigate" />
      <div v-if="session.state.wsError" class="banner" role="alert">
        <span class="banner__seal">!</span>
        <div class="banner__body">
          <strong>操作失败</strong>
          <span>{{ session.state.wsError }}</span>
        </div>
        <button class="banner__close" @click="session.dismissError">×</button>
      </div>

      <!-- 预习 / 复习：自适应试题推送（EduBrain） -->
      <AdaptiveQuiz
        v-if="activeView !== 'case'"
        :mode="activeView === 'preview' ? 'diagnostic' : 'review'"
        @back="navigate('case')"
      />

      <!-- 精学：案件工作台（案例教学法） -->
      <main v-else class="stage-screen">
        <!-- 左：状态机阶段轨 -->
        <aside class="stage-screen__rail">
          <StageRail />
        </aside>

        <!-- 中：场景图 + 底部对话框 -->
        <section class="stage-screen__center">
          <div class="stage-screen__visual">
            <StageBackdrop />
          </div>
          <div class="stage-screen__dialog">
            <DialogueFeed />
          </div>
        </section>

        <!-- 右：工具调用面板 -->
        <aside class="stage-screen__tools">
          <ToolPanel />
        </aside>
      </main>
    </template>
    <AuthPanel v-if="!isLoggedIn" />
  </div>
</template>

<style scoped>
.app-shell {
  height: 100vh;
  max-height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.banner {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 8px 28px;
  background: linear-gradient(
    90deg,
    rgba(196, 71, 27, 0.16),
    rgba(196, 71, 27, 0.04)
  );
  border-bottom: 1px solid rgba(196, 71, 27, 0.4);
  font-size: 0.86rem;
  flex-shrink: 0;
}
.banner__seal {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  display: grid;
  place-items: center;
  font-family: var(--font-display);
  font-weight: 700;
  flex-shrink: 0;
}
.banner__body {
  flex: 1;
  display: flex;
  gap: 10px;
  align-items: baseline;
  color: #f0b6a6;
}
.banner__body strong {
  color: var(--accent);
  font-family: var(--font-display);
  font-weight: 600;
}
.banner__close {
  background: transparent;
  border: 1px solid var(--line-strong);
  color: var(--parchment-muted);
  width: 26px;
  height: 26px;
  border-radius: 2px;
  cursor: pointer;
  font-size: 1.1rem;
  line-height: 1;
}
.banner__close:hover {
  border-color: var(--accent);
  color: var(--accent);
}

/* ── 三栏：左阶段轨 / 中图片+对话 / 右工具面板 ── */
.stage-screen {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 218px minmax(0, 1fr) 300px;
}

.stage-screen__rail {
  min-height: 0;
  overflow-y: auto;
  border-right: 1px solid var(--line);
  background: linear-gradient(180deg, rgba(20, 17, 11, 0.92), rgba(14, 12, 8, 0.96));
  padding: 14px 12px;
}

.stage-screen__center {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.stage-screen__visual {
  flex: 1;
  min-height: 0;
  position: relative;
  overflow: hidden;
}

.stage-screen__dialog {
  flex-shrink: 0;
  height: 42vh;
  min-height: 300px;
  position: relative;
  border-top: 1px solid var(--line-strong);
  background: linear-gradient(180deg, var(--ink-750), var(--ink-900));
}

.stage-screen__tools {
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--line);
  background: linear-gradient(180deg, rgba(20, 17, 11, 0.92), rgba(14, 12, 8, 0.96));
  padding: 14px 14px 10px;
}

@media (max-width: 1280px) {
  .stage-screen {
    grid-template-columns: 190px minmax(0, 1fr) 250px;
  }
}
@media (max-width: 1020px) {
  .stage-screen {
    grid-template-columns: 178px minmax(0, 1fr);
  }
  .stage-screen__tools {
    display: none;
  }
}
</style>
