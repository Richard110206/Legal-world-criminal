<script setup lang="ts">
import { computed } from "vue";
import { useSession } from "../composables/useSession";
import { agentDisplayName, roleName } from "../lib/roleNames";

const session = useSession();
const agents = computed(() => session.agents.value);

function initials(name: string): string {
  if (!name) return "?";
  // Use first character (works for both Chinese and Latin)
  return name.trim().charAt(0).toUpperCase();
}

function hueFor(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) {
    h = (h * 31 + id.charCodeAt(i)) % 360;
  }
  return h;
}

function ago(ts: number): string {
  const sec = Math.round((Date.now() - ts) / 1000);
  if (sec < 5) return "刚刚";
  if (sec < 60) return `${sec}秒前`;
  if (sec < 3600) return `${Math.round(sec / 60)}分钟前`;
  return `${Math.round(sec / 3600)}小时前`;
}
</script>

<template>
  <section class="roster">
    <header class="roster__head">
      <p class="kicker">DRAMATIS PERSONAE</p>
      <h3>角色名册</h3>
      <span class="tag roster__count">{{ agents.length }}</span>
    </header>

    <ul v-if="agents.length" class="roster__list">
      <li v-for="agent in agents" :key="agent.agent_id" class="agent">
        <div
          class="agent__avatar"
          :style="{
            background: `linear-gradient(135deg, hsl(${hueFor(agent.agent_id)} 30% 30%), hsl(${hueFor(agent.agent_id)} 40% 18%))`,
            borderColor: `hsl(${hueFor(agent.agent_id)} 40% 50% / 0.4)`,
          }"
        >
          {{ initials(agent.name) }}
        </div>
        <div class="agent__body">
          <div class="agent__name">
            {{ agentDisplayName(agent.name, agent.role, agent.character_name) }}
          </div>
          <div class="agent__meta">
            <span v-if="agent.role" class="agent__role tag">{{ roleName(agent.role) }}</span>
            <span class="agent__time mono">{{ ago(agent.last_active_at) }}</span>
          </div>
          <p v-if="agent.last_bubble" class="agent__bubble">
            {{ agent.last_bubble }}
          </p>
        </div>
      </li>
    </ul>

    <p v-else class="roster__empty muted">
      尚无角色入场。启动模拟后,角色名册将在此列出。
    </p>
  </section>
</template>

<style scoped>
.roster {
  display: flex;
  flex-direction: column;
}

.roster__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
}
.kicker {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 0.62rem;
  letter-spacing: 0.2em;
  color: var(--accent);
  flex-basis: 100%;
}
.roster__head h3 {
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 1.05rem;
  font-weight: 600;
  margin: 0;
  flex: 1;
}
.roster__count { font-size: 0.7rem; }

.roster__list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.agent {
  display: grid;
  grid-template-columns: 36px 1fr;
  gap: 10px;
  padding: 10px;
  background: rgba(255, 255, 255, 0.015);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  animation: ink-rise 0.35s ease both;
}

.agent__avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 1rem;
  color: var(--parchment);
  border: 1px solid;
}

.agent__name {
  font-family: "Noto Serif SC", var(--font-display);
  font-weight: 600;
  font-size: 0.92rem;
  color: var(--parchment);
  line-height: 1.3;
}

.agent__meta {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-top: 2px;
}
.agent__role { font-size: 0.62rem; padding: 1px 5px; }
.agent__time { font-size: 0.66rem; color: var(--parchment-dim); }

.agent__bubble {
  margin: 6px 0 0;
  padding: 6px 8px;
  font-size: 0.78rem;
  color: var(--parchment-muted);
  background: rgba(0, 0, 0, 0.2);
  border-left: 2px solid var(--accent-amber);
  line-height: 1.4;
  font-style: italic;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.roster__empty {
  font-size: 0.8rem;
  font-style: italic;
  padding: 10px 4px;
}
</style>
