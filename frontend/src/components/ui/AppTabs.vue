<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: [String, Number], default: '' },
  tabs: { type: Array, required: true },
  variant: { type: String, default: 'paper' },
  ariaLabel: { type: String, default: '视图切换' },
})

const emit = defineEmits(['update:modelValue'])
const list = ref(null)
const buttons = ref([])
const thumbStyle = ref({ width: '0px', transform: 'translateX(0)' })
let resizeObserver = null

const activeIndex = computed(() => {
  const index = props.tabs.findIndex((tab) => tab.id === props.modelValue)
  return index >= 0 ? index : 0
})

function updateThumb() {
  const el = list.value
  const active = buttons.value[activeIndex.value]
  if (!el || !active) return
  thumbStyle.value = {
    width: `${active.offsetWidth}px`,
    transform: `translateX(${active.offsetLeft}px)`,
  }
}

async function refreshThumb() {
  await nextTick()
  updateThumb()
}

watch([() => props.modelValue, () => props.tabs], refreshThumb, { deep: true })

onMounted(() => {
  refreshThumb()
  if (typeof ResizeObserver !== 'undefined' && list.value) {
    resizeObserver = new ResizeObserver(updateThumb)
    resizeObserver.observe(list.value)
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
})

function select(tab) {
  if (tab.disabled) return
  emit('update:modelValue', tab.id)
}

async function onKeydown(event) {
  const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End']
  if (!keys.includes(event.key)) return
  event.preventDefault()

  const enabled = props.tabs
    .map((tab, index) => ({ tab, index }))
    .filter((item) => !item.tab.disabled)
  if (!enabled.length) return

  const current = Math.max(0, enabled.findIndex((item) => item.index === activeIndex.value))
  let next = current
  if (event.key === 'ArrowLeft') next = (current - 1 + enabled.length) % enabled.length
  if (event.key === 'ArrowRight') next = (current + 1) % enabled.length
  if (event.key === 'Home') next = 0
  if (event.key === 'End') next = enabled.length - 1

  const target = enabled[next]
  emit('update:modelValue', target.tab.id)
  await nextTick()
  buttons.value[target.index]?.focus()
}
</script>

<template>
  <div
    ref="list"
    class="tabs"
    :class="`tabs--${variant}`"
    role="tablist"
    :aria-label="ariaLabel"
    @keydown="onKeydown"
  >
    <span class="tabs__thumb" :style="thumbStyle" aria-hidden="true"></span>
    <button
      v-for="tab in tabs"
      :key="tab.id"
      ref="buttons"
      class="tabs__key"
      :class="{ 'is-active': tab.id === modelValue }"
      type="button"
      role="tab"
      :aria-selected="tab.id === modelValue"
      :tabindex="tab.id === modelValue ? 0 : -1"
      :disabled="tab.disabled"
      @click="select(tab)"
    >
      <span class="tabs__dot" aria-hidden="true"></span>
      {{ tab.label }}
    </button>
  </div>
</template>

<style scoped>
.tabs {
  position: relative;
  display: inline-flex;
  gap: 2px;
  max-width: 100%;
  padding: 3px;
  border-radius: var(--radius-pill);
  overflow-x: auto;
  scrollbar-width: none;
}

.tabs__thumb {
  position: absolute;
  top: 3px;
  bottom: 3px;
  left: 0;
  z-index: 0;
  background: var(--paper-raised);
  border-radius: var(--radius-pill);
  box-shadow:
    0 1px 1px rgb(31 51 45 / 0.06),
    0 2px 6px rgb(31 51 45 / 0.1);
  pointer-events: none;
  transition:
    transform var(--dur-2) var(--ease-out),
    width var(--dur-2) var(--ease-out);
}

.tabs--instrument .tabs__thumb {
  background: var(--instrument-raised);
  box-shadow:
    0 1px 1px rgb(31 51 45 / 0.06),
    0 2px 6px rgb(31 51 45 / 0.1);
}

.tabs::-webkit-scrollbar {
  display: none;
}

.tabs--instrument {
  background: var(--instrument);
  border: 1px solid var(--rule);
}

.tabs--paper {
  background: var(--paper-deep);
  border: 1px solid var(--rule);
  box-shadow: inset 0 1px 2px rgb(31 51 45 / 0.06);
}

.tabs__key {
  position: relative;
  z-index: 1;
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 7px;
  min-height: var(--control-md);
  padding: 0 13px;
  color: var(--text-muted);
  background: transparent;
  border: 0;
  border-radius: var(--radius-pill);
  font-family: var(--font-mono);
  font-size: var(--type-label);
  letter-spacing: 0.02em;
  white-space: nowrap;
  transition: color var(--quick) var(--ease);
}

.tabs--instrument .tabs__key {
  color: var(--instrument-muted);
}

.tabs__key:hover:not(:disabled) {
  color: var(--ink);
}

.tabs--instrument .tabs__key:hover:not(:disabled) {
  color: var(--instrument-text);
}

.tabs__key:focus-visible {
  outline: 2px solid var(--patina-deep);
  outline-offset: 4px;
}

.tabs__dot {
  width: 5px;
  height: 5px;
  background: var(--gray-2);
  border-radius: 50%;
  transition: background-color var(--quick) var(--ease);
}

.tabs--instrument .tabs__dot {
  background: var(--gray-2);
}

.tabs__key.is-active {
  color: var(--ink);
}

.tabs--instrument .tabs__key.is-active {
  color: var(--instrument-text);
}

.tabs__key.is-active .tabs__dot {
  background: var(--accent);
}

.tabs__key:disabled {
  color: var(--text-disabled);
  cursor: not-allowed;
}

@media (max-width: 760px) {
  .tabs {
    width: 100%;
  }

  .tabs__key {
    flex: 1;
    justify-content: center;
  }
}
</style>
