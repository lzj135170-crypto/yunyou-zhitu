<script setup>
import AppTabs from '../ui/AppTabs.vue'
import Icon from '../ui/Icon.vue'
import BrandMark from './BrandMark.vue'

defineProps({
  user: { type: Object, default: null },
  avatarText: { type: String, default: '?' },
  view: { type: String, default: 'plan' },
  hasResult: { type: Boolean, default: false },
})

const emit = defineEmits(['navigate', 'logout'])

const tabs = [
  { id: 'plan', label: '规划' },
  { id: 'result', label: '结果' },
  { id: 'history', label: '历史' },
]
</script>

<template>
  <header class="app-header" data-print-hide>
    <div class="app-header__inner container">
      <button class="brand" type="button" aria-label="回到规划页" @click="emit('navigate', 'plan')">
        <BrandMark :size="27" class="brand__mark" />
        <span class="brand__text">
          <span class="brand__name">云游织图</span>
          <span class="brand__latin">YUNYOU ZHITU</span>
        </span>
      </button>

      <div class="app-header__tabs">
        <AppTabs
          :model-value="view"
          :tabs="tabs.map((tab) => ({ ...tab, disabled: tab.id === 'result' && !hasResult }))"
          variant="instrument"
          aria-label="主导航"
          @update:model-value="emit('navigate', $event)"
        />
      </div>

      <div class="app-header__user">
        <span class="avatar" aria-hidden="true">{{ avatarText }}</span>
        <span class="username">{{ user ? user.username : '' }}</span>
        <button class="logout" type="button" @click="emit('logout')">
          <Icon name="logout" :size="16" />
          <span>退出</span>
        </button>
      </div>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  position: sticky;
  top: 0;
  z-index: 40;
  background: rgb(248 251 246 / 0.92);
  border-bottom: 1px solid var(--rule);
  backdrop-filter: blur(10px);
}

.app-header__inner {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 24px;
  align-items: center;
  min-height: 72px;
}

.brand {
  display: inline-flex;
  gap: 10px;
  align-items: center;
  padding: 4px 0;
  color: var(--ink);
  background: transparent;
  border: 0;
  text-align: left;
}

.brand__mark {
  color: var(--ink);
}

.brand__text {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.brand__name {
  font-family: var(--font-display);
  font-size: 1.28rem;
  font-weight: 500;
  line-height: 1.1;
  letter-spacing: 0.08em;
}

.brand__latin {
  color: var(--text-faint);
  font-family: var(--font-mono);
  font-size: 0.58rem;
  letter-spacing: 0.16em;
}

.app-header__tabs {
  display: flex;
  justify-content: center;
  min-width: 0;
}

.app-header__user {
  display: flex;
  gap: 9px;
  align-items: center;
  justify-content: flex-end;
}

.avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  color: var(--instrument-text);
  background: var(--instrument);
  border: 1px solid var(--accent-deep);
  border-radius: 50%;
  font-family: var(--font-mono);
  font-size: var(--type-label);
}

.username {
  max-width: 12ch;
  overflow: hidden;
  color: var(--text-muted);
  font-size: var(--type-small);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logout {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  min-height: 30px;
  padding: 0 8px;
  color: var(--text-muted);
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  font-size: var(--type-label);
}

.logout:hover {
  color: var(--ink);
  background: var(--paper-deep);
}

@media (max-width: 900px) {
  .app-header__inner {
    grid-template-columns: auto auto;
    gap: 12px;
    padding-top: 12px;
    padding-bottom: 12px;
  }

  .app-header__tabs {
    grid-column: 1 / -1;
    grid-row: 2;
    justify-content: flex-start;
  }

  .app-header__tabs :deep(.tabs) {
    width: 100%;
  }

  .app-header__tabs :deep(.tabs__key) {
    flex: 1;
    justify-content: center;
  }

  .username {
    display: none;
  }
}

@media (max-width: 520px) {
  .brand__latin {
    display: none;
  }

  .brand__name {
    font-size: 1.1rem;
  }

  .logout span {
    display: none;
  }
}
</style>
