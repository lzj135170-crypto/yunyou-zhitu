<script setup>
defineProps({
  active: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['click'])
</script>

<template>
  <button
    class="chip"
    :class="{ 'chip--active': active }"
    type="button"
    :aria-pressed="active"
    :disabled="disabled"
    @click="emit('click')"
  >
    <span class="chip__dot" aria-hidden="true"></span>
    <slot />
  </button>
</template>

<style scoped>
.chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 32px;
  padding: 0 11px;
  color: var(--text-muted);
  background: var(--paper-raised);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
  font-size: var(--type-label);
  line-height: 1;
  transition:
    color var(--quick) var(--ease),
    border-color var(--quick) var(--ease),
    background-color var(--quick) var(--ease);
}

.chip:hover:not(:disabled) {
  color: var(--ink);
  background: var(--paper-deep);
  border-color: var(--edge);
}

.chip__dot {
  width: 5px;
  height: 5px;
  background: var(--gray-2);
  border-radius: 50%;
  transition: background-color var(--quick) var(--ease);
}

.chip--active {
  color: var(--accent-deep);
  background: var(--instrument);
  border-color: var(--accent-strong);
}

.chip--active .chip__dot {
  background: var(--accent);
  box-shadow: 0 0 0 2px rgb(31 111 92 / 0.14);
}

.chip:disabled {
  color: var(--text-disabled);
  cursor: not-allowed;
}
</style>
