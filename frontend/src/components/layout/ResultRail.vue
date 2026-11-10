<script setup>
import { nextTick, ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  sections: { type: Array, default: () => [] },
  activeSection: { type: String, default: '' },
})

const nav = ref(null)

watch(
  () => props.activeSection,
  async (id) => {
    if (!id) return
    await nextTick()
    const element = nav.value
    if (!element || element.scrollWidth <= element.clientWidth) return
    const active = element.querySelector('.rail-link.is-active')
    if (!active) return
    const left = active.offsetLeft - (element.clientWidth - active.clientWidth) / 2
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    element.scrollTo({ left, behavior: reduce ? 'auto' : 'smooth' })
  }
)

const emit = defineEmits(['select', 'action'])

const actions = [
  { id: 'plan', label: '返回规划页', icon: 'arrow-left' },
  { id: 'new', label: '新建行程', icon: 'plus' },
  { id: 'history', label: '历史列表', icon: 'history' },
  { id: 'markdown', label: '导出 Markdown', icon: 'download' },
  { id: 'print', label: '导出 PDF', icon: 'printer' },
]
</script>

<template>
  <aside class="result-rail" data-print-hide>
    <p class="result-rail__label mono">行程目录</p>
    <nav ref="nav" class="result-rail__nav" aria-label="行程目录">
      <button
        v-for="item in sections"
        :key="item.id"
        class="rail-link"
        :class="{ 'is-active': activeSection === item.id }"
        type="button"
        :aria-current="activeSection === item.id ? 'location' : undefined"
        @click="emit('select', item.id)"
      >
        <Icon :name="item.icon || 'compass'" :size="15" class="rail-link__icon" />
        <span>{{ item.label }}</span>
      </button>
    </nav>

    <div class="result-rail__rule"></div>

    <div class="result-rail__actions">
      <button
        v-for="action in actions"
        :key="action.id"
        class="rail-action"
        type="button"
        @click="emit('action', action.id)"
      >
        <Icon :name="action.icon" :size="15" />
        <span>{{ action.label }}</span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.result-rail {
  position: sticky;
  top: 96px;
  align-self: start;
  width: 200px;
  min-width: 0;
  max-width: 100%;
  max-height: calc(100vh - 120px);
  padding-right: 20px;
  overflow-y: auto;
  border-right: 1px solid var(--rule);
}

.result-rail__label {
  margin-bottom: 10px;
  color: var(--text-faint);
}

.result-rail__nav {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.rail-link {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 6px 0 6px 12px;
  color: var(--text-muted);
  background: transparent;
  border: 0;
  border-left: 2px solid transparent;
  font-family: var(--font-ui);
  font-size: var(--type-small);
  line-height: 1.5;
  text-align: left;
  transition:
    color var(--quick) var(--ease),
    border-color var(--quick) var(--ease),
    background-color var(--quick) var(--ease);
}

.rail-link__icon {
  flex: none;
  color: var(--text-faint);
  transition: color var(--quick) var(--ease);
}

.rail-link:hover {
  color: var(--ink);
}

.rail-link:hover .rail-link__icon,
.rail-link.is-active .rail-link__icon {
  color: var(--accent);
}

.rail-link.is-active {
  color: var(--ink);
  background: linear-gradient(90deg, rgb(31 111 92 / 0.09), transparent 85%);
  border-left-color: var(--accent);
  font-weight: 600;
}

.result-rail__rule {
  height: 1px;
  margin: 14px 0;
  background: var(--rule);
}

.result-rail__actions {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.rail-action {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 6px 0 6px 12px;
  color: var(--text-muted);
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  font-size: var(--type-label);
  text-align: left;
}

.rail-action:hover {
  color: var(--ink);
  background: var(--paper-deep);
}

@media (max-width: 980px) {
  .result-rail {
    position: static;
    width: 100%;
    max-height: none;
    padding: 0 0 10px;
    overflow: visible;
    border-right: 0;
    border-bottom: 1px solid var(--rule);
  }

  .result-rail__label {
    display: none;
  }

  .result-rail__nav {
    flex-direction: row;
    gap: 2px;
    max-width: 100%;
    overflow-x: auto;
    scrollbar-width: none;
  }

  .result-rail__nav::-webkit-scrollbar {
    display: none;
  }

  .rail-link {
    flex: none;
    padding: 7px 10px;
    border-left: 0;
    border-bottom: 2px solid transparent;
    white-space: nowrap;
  }

  .rail-link.is-active {
    background: transparent;
    border-bottom-color: var(--accent);
  }

  .result-rail__rule {
    display: none;
  }

  .result-rail__actions {
    flex-direction: row;
    flex-wrap: wrap;
    gap: 4px 8px;
    max-width: 100%;
    padding-top: 8px;
  }

  .rail-action {
    flex: none;
    padding: 5px 8px;
    white-space: nowrap;
  }
}
</style>
