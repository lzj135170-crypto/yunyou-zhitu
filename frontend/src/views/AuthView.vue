<script setup>
import AppButton from '../components/ui/AppButton.vue'
import AppInput from '../components/ui/AppInput.vue'
import AppTabs from '../components/ui/AppTabs.vue'
import BrandMark from '../components/layout/BrandMark.vue'

defineProps({
  authMode: { type: String, default: 'login' },
  authForm: { type: Object, required: true },
  authError: { type: String, default: '' },
  authLoading: { type: Boolean, default: false },
})

const emit = defineEmits(['switch-mode', 'submit'])

const tabs = [
  { id: 'login', label: '登录' },
  { id: 'register', label: '注册' },
]

const capabilities = [
  {
    title: '多智能体编排',
    detail: '景点、美食、住宿、交通、天气分别处理，最后统一成一份按天行程。',
  },
  {
    title: 'RAG 出处回溯',
    detail: '知识库引用带标题与章节；资料里没有的数字不会被写成确定事实。',
  },
  {
    title: '事实 MCP 校验',
    detail: '地图、天气和地点信息走真实工具查询，不靠模型记忆补全。',
  },
  {
    title: '生成后检查',
    detail: '行程生成后再校验日期、天数和地点一致性，减少跑不通的安排。',
  },
]
</script>

<template>
  <main id="main" class="auth-page" tabindex="-1">
    <section class="auth-brand">
      <div class="auth-brand__lockup">
        <BrandMark :size="38" animated class="auth-brand__mark" />
        <span class="auth-brand__name">云游织图</span>
        <span class="auth-brand__edition mono">ATLAS / 01</span>
      </div>

      <p class="auth-brand__eyebrow mono">日光手帐 · 旅行仪表盘</p>
      <h1>把目的地、时间和偏好，织成一份能照着走的行程。</h1>
      <p class="auth-brand__sub">
        景点、住宿、交通、天气各自查清，再汇总成按天编排的行程；资料里有出处的写清楚，没有的标成估算。
      </p>

      <ol class="auth-route" aria-label="行程生成流程">
        <li v-for="(item, index) in capabilities" :key="item.title" class="auth-stop">
          <span class="auth-stop__index mono" aria-hidden="true">
            {{ String(index + 1).padStart(2, '0') }}
          </span>
          <div class="auth-stop__body">
            <strong>{{ item.title }}</strong>
            <p>{{ item.detail }}</p>
          </div>
        </li>
      </ol>
    </section>

    <section class="auth-card">
      <header class="auth-card__head">
        <p class="auth-card__eyebrow mono">ACCESS / {{ authMode === 'login' ? 'SIGN IN' : 'REGISTER' }}</p>
        <h2>{{ authMode === 'login' ? '欢迎回来' : '创建账号' }}</h2>
        <p>
          {{ authMode === 'login' ? '登录后继续你的旅行计划。' : '注册后即可开始规划。' }}
        </p>
      </header>

      <AppTabs
        :model-value="authMode"
        :tabs="tabs"
        variant="instrument"
        aria-label="登录或注册"
        class="auth-card__tabs"
        @update:model-value="emit('switch-mode', $event)"
      />

      <form class="auth-form" @submit.prevent="emit('submit')">
        <AppInput
          v-model="authForm.username"
          label="账号"
          placeholder="3–50 个字符"
          autocomplete="username"
        />
        <AppInput
          v-model="authForm.password"
          label="密码"
          type="password"
          placeholder="至少 6 位"
          :autocomplete="authMode === 'login' ? 'current-password' : 'new-password'"
        />

        <template v-if="authMode === 'register'">
          <AppInput v-model="authForm.email" label="邮箱（选填）" placeholder="name@example.com" />
          <AppInput v-model="authForm.phone" label="手机号（选填）" placeholder="选填" />
        </template>

        <p v-if="authError" class="auth-form__error" role="alert">{{ authError }}</p>

        <AppButton type="submit" variant="primary" size="lg" block :loading="authLoading" arrow>
          {{ authLoading ? '处理中…' : authMode === 'login' ? '登录' : '注册并登录' }}
        </AppButton>
      </form>

      <p class="auth-card__note">
        登录后，行程和本地保存的酒店地址只属于当前账号。
      </p>
    </section>
  </main>
</template>

<style scoped>
.auth-page {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(360px, 0.95fr);
  gap: clamp(32px, 6vw, 80px);
  align-items: center;
  width: 100%;
  max-width: 1180px;
  min-height: 100vh;
  margin: 0 auto;
  padding: clamp(32px, 6vw, 72px) var(--gutter);
}

.auth-page::before {
  position: absolute;
  top: 0;
  left: 0;
  width: min(48vw, 560px);
  height: 1px;
  background: linear-gradient(90deg, var(--accent), transparent);
  content: "";
  opacity: 0.55;
}

.auth-brand {
  min-width: 0;
  max-width: 560px;
}

.auth-brand__lockup {
  display: flex;
  gap: 11px;
  align-items: center;
  margin-bottom: clamp(28px, 5vw, 56px);
  color: var(--ink);
}

.auth-brand__mark {
  color: var(--ink);
}

.auth-brand__name {
  font-family: var(--font-display);
  font-size: 1.45rem;
  font-weight: 500;
  letter-spacing: 0.08em;
}

.auth-brand__edition {
  margin-left: auto;
  color: var(--text-faint);
  font-size: 0.58rem;
  letter-spacing: 0.16em;
}

.auth-brand__eyebrow {
  color: var(--accent-deep);
  font-size: var(--type-micro);
  letter-spacing: 0.16em;
}

.auth-brand h1 {
  max-width: 100%;
  margin-top: 12px;
  color: var(--ink);
  font-family: var(--font-display);
  font-size: clamp(1.45rem, 7vw, 2.7rem);
  font-weight: 300;
  line-height: 1.14;
  letter-spacing: 0.01em;
  word-break: keep-all;
  overflow-wrap: anywhere;
}

.auth-brand__sub {
  max-width: 52ch;
  margin-top: 18px;
  color: var(--text-muted);
  font-size: var(--type-body);
  line-height: 1.8;
}

.auth-route {
  position: relative;
  display: flex;
  flex-direction: column;
  margin-top: clamp(30px, 4vw, 48px);
}

.auth-route::before {
  position: absolute;
  top: 18px;
  bottom: 18px;
  left: 13px;
  width: 1px;
  background: linear-gradient(var(--accent), var(--patina-oxide) 68%, transparent);
  content: "";
  opacity: 0.42;
}

.auth-stop {
  position: relative;
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr);
  gap: 15px;
  padding: 13px 0;
  border-bottom: 1px solid var(--rule);
}

.auth-stop:last-child {
  border-bottom: 0;
}

.auth-stop__index {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  color: var(--accent);
  background: var(--paper);
  border: 1px solid var(--accent-deep);
  border-radius: 50%;
  box-shadow: 0 0 0 3px var(--paper);
  font-size: 0.58rem;
  letter-spacing: 0.04em;
}

.auth-stop__body {
  padding-top: 2px;
}

.auth-stop__body strong {
  display: block;
  color: var(--ink);
  font-size: var(--type-small);
  font-weight: 650;
}

.auth-stop__body p {
  max-width: 48ch;
  margin-top: 3px;
  color: var(--text-muted);
  font-size: var(--type-small);
  line-height: 1.65;
}

.auth-card {
  position: relative;
  min-width: 0;
  padding: clamp(24px, 3vw, 34px);
  background: var(--paper-raised);
  border: 1px solid var(--rule-strong);
  border-radius: var(--radius-md);
  box-shadow: var(--lift-1);
}

.auth-card::before {
  position: absolute;
  top: -1px;
  right: 34px;
  left: 34px;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  content: "";
  opacity: 0.7;
}

.auth-card__eyebrow {
  color: var(--text-faint);
  font-size: 0.6rem;
  letter-spacing: 0.15em;
}

.auth-card__head h2 {
  margin-top: 7px;
  color: var(--ink);
  font-family: var(--font-display);
  font-size: 2.1rem;
  font-weight: 300;
  line-height: 1.15;
}

.auth-card__head p {
  margin-top: 4px;
  color: var(--text-muted);
  font-size: var(--type-small);
}

.auth-card__tabs {
  margin: 24px 0 22px;
}

.auth-card__tabs :deep(.tabs) {
  width: 100%;
}

.auth-card__tabs :deep(.tabs__key) {
  flex: 1;
  justify-content: center;
}

.auth-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.auth-form__error {
  padding: 9px 11px;
  color: var(--vermilion);
  background: var(--vermilion-soft);
  border: 1px solid rgb(192 73 61 / 0.28);
  border-radius: var(--radius-sm);
  font-size: var(--type-small);
}

.auth-card__note {
  margin-top: 18px;
  color: var(--text-faint);
  font-size: var(--type-micro);
  line-height: 1.6;
}

@media (max-width: 900px) {
  .auth-page {
    grid-template-columns: 1fr;
    min-height: auto;
    padding-top: 40px;
    padding-bottom: 56px;
  }

  .auth-brand h1 {
    max-width: 100%;
  }

  .auth-brand__lockup {
    margin-bottom: 26px;
  }

  .auth-card {
    width: 100%;
    max-width: 520px;
  }
}

@media (max-width: 520px) {
  .auth-brand__edition {
    display: none;
  }

  .auth-brand h1 {
    max-width: none;
  }
}
</style>
