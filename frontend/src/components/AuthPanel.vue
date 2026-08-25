<script setup lang="ts">
import { computed, ref } from "vue";
import { useSession } from "../composables/useSession";

const session = useSession();
const wsHost = computed(() => window.location.host);

const mode = ref<"login" | "register">("register");
const email = ref("");
const password = ref("");
const error = ref<string | null>(null);
const busy = ref(false);

async function submit() {
  if (!email.value || !password.value) {
    error.value = "请填写邮箱和密码";
    return;
  }
  if (password.value.length < 6) {
    error.value = "密码至少 6 位";
    return;
  }
  error.value = null;
  busy.value = true;
  try {
    if (mode.value === "register") {
      await session.register(email.value, password.value);
    } else {
      await session.login(email.value, password.value);
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    busy.value = false;
  }
}

function toggleMode() {
  mode.value = mode.value === "login" ? "register" : "login";
  error.value = null;
}
</script>

<template>
  <div class="hero">
    <div class="hero__bg" aria-hidden="true">
      <div class="hero__stain hero__stain--1"></div>
      <div class="hero__stain hero__stain--2"></div>
      <div class="hero__grid"></div>
    </div>

    <section class="gate reveal">
      <header class="gate__mast">
        <div class="gate__seal">
          <span>法</span>
        </div>
        <div class="gate__title">
          <p class="gate__kicker">LEGALWORLD · CASE OBSERVATORY</p>
          <h1>案例观察台</h1>
          <p class="gate__sub muted">
            A life-cycle portal for legal agents — consultation, drafting,
            first-instance, appeal, second-instance.
          </p>
        </div>
      </header>

      <div class="rule"></div>

      <form class="gate__form" @submit.prevent="submit">
        <label class="field">
          <span>邮箱 / Email</span>
          <input
            v-model="email"
            class="input"
            type="email"
            autocomplete="email"
            placeholder="you@court.edu"
          />
        </label>
        <label class="field">
          <span>密码 / Password</span>
          <input
            v-model="password"
            class="input"
            type="password"
            autocomplete="current-password"
            placeholder="至少 6 位"
          />
        </label>

        <p v-if="error" class="gate__error">{{ error }}</p>

        <div class="row" style="margin-top: 6px">
          <button class="btn btn--primary" type="submit" :disabled="busy">
            {{ busy ? "提交中…" : mode === "register" ? "注册并进入" : "登录" }}
          </button>
          <button class="btn btn--ghost" type="button" @click="toggleMode">
            {{ mode === "register" ? "已有账号? 登录" : "新用户? 注册" }}
          </button>
        </div>
      </form>

      <footer class="gate__foot muted">
        <span class="mono">{{ session.backendVersion.value ?? "—" }}</span>
        <span class="dim">·</span>
        <span>WebSocket 监听 ws://{{ wsHost }}/ws</span>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.hero {
  position: relative;
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 48px 28px;
  overflow: hidden;
}

.hero__bg {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.hero__stain {
  position: absolute;
  border-radius: 50%;
  filter: blur(80px);
  opacity: 0.5;
}
.hero__stain--1 {
  width: 540px;
  height: 540px;
  background: radial-gradient(circle, rgba(196, 71, 27, 0.4), transparent 60%);
  top: -120px;
  right: -80px;
}
.hero__stain--2 {
  width: 420px;
  height: 420px;
  background: radial-gradient(circle, rgba(176, 138, 62, 0.25), transparent 60%);
  bottom: -120px;
  left: -60px;
}

.hero__grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(to right, rgba(236, 228, 211, 0.03) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(236, 228, 211, 0.03) 1px, transparent 1px);
  background-size: 64px 64px;
  mask-image: radial-gradient(ellipse at center, black, transparent 75%);
}

.gate {
  position: relative;
  width: 100%;
  max-width: 480px;
  padding: 36px 36px 28px;
  background: linear-gradient(180deg, var(--ink-750), var(--ink-800));
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  box-shadow:
    0 1px 0 rgba(255, 240, 210, 0.04) inset,
    0 60px 120px -50px rgba(0, 0, 0, 0.9);
}
/* Corner registration marks */
.gate::before,
.gate::after {
  content: "";
  position: absolute;
  width: 14px;
  height: 14px;
  border: 1px solid var(--accent);
}
.gate::before {
  top: 10px;
  left: 10px;
  border-right: none;
  border-bottom: none;
}
.gate::after {
  bottom: 10px;
  right: 10px;
  border-left: none;
  border-top: none;
}

.gate__mast {
  display: flex;
  align-items: flex-start;
  gap: 18px;
}

.gate__seal {
  width: 56px;
  height: 56px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  display: grid;
  place-items: center;
  font-family: var(--font-display);
  font-size: 1.6rem;
  font-weight: 700;
  box-shadow: 0 0 0 4px var(--ink-800), 0 0 0 5px var(--accent-soft);
  transform: rotate(-6deg);
}
.gate__seal span {
  font-family: "Noto Serif SC", var(--font-display);
  font-weight: 900;
}

.gate__title { flex: 1; padding-top: 4px; }

.gate__kicker {
  margin: 0 0 6px;
  font-family: var(--font-mono);
  font-size: 0.7rem;
  letter-spacing: 0.2em;
  color: var(--accent);
}
.gate__title h1 {
  font-family: "Noto Serif SC", var(--font-display);
  font-size: 2rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  margin: 0 0 6px;
}
.gate__sub {
  margin: 0;
  font-size: 0.86rem;
  font-style: italic;
  line-height: 1.5;
  font-family: var(--font-body);
  font-variation-settings: "opsz" 14;
}

.gate__form { margin-top: 4px; }

.gate__error {
  margin: 0 0 12px;
  padding: 8px 12px;
  background: rgba(168, 52, 31, 0.12);
  border-left: 2px solid var(--accent);
  color: #f0b6a6;
  font-size: 0.86rem;
}

.gate__foot {
  margin-top: 22px;
  padding-top: 14px;
  border-top: 1px dashed var(--line);
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 0.76rem;
}
</style>
