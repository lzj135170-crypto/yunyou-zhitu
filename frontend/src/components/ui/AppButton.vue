<script setup>
import Icon from './Icon.vue'
import Spinner from './Spinner.vue'

defineProps({
  variant: { type: String, default: 'primary' },
  size: { type: String, default: 'md' },
  loading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  arrow: { type: Boolean, default: false },
  type: { type: String, default: 'button' },
})
</script>

<template>
  <button
    class="btn"
    :class="[`btn--${variant}`, `btn--${size}`]"
    :type="type"
    :disabled="disabled || loading"
    :aria-busy="loading || undefined"
  >
    <Spinner v-if="loading" :size="15" />
    <slot />
    <Icon v-if="arrow && !loading" name="arrow-right" :size="16" class="btn__arrow" />
  </button>
</template>

<style scoped>
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  min-height: var(--control-lg);
  padding: 0 18px;
  color: var(--ink);
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  font-family: var(--font-ui);
  font-size: var(--type-ui);
  font-weight: 550;
  line-height: 1;
  text-decoration: none;
  white-space: nowrap;
  transition:
    background-color var(--quick) var(--ease),
    border-color var(--quick) var(--ease),
    color var(--quick) var(--ease),
    transform var(--quick) var(--ease),
    box-shadow var(--quick) var(--ease);
}

.btn:focus-visible {
  outline: 2px solid var(--patina-deep);
  outline-offset: 4px;
}

.btn:disabled {
  color: var(--text-disabled);
  cursor: not-allowed;
  transform: none;
}

.btn--primary {
  color: var(--on-accent);
  background: var(--accent);
  border-color: var(--accent);
}

.btn--primary:hover:not(:disabled) {
  background: var(--accent-strong);
  border-color: var(--accent-strong);
  transform: translateY(-1px);
  box-shadow: var(--glow-accent);
}

.btn--primary:active:not(:disabled) {
  transform: translateY(0);
}

.btn--primary .btn__arrow {
  color: var(--on-accent);
}

.btn--primary:disabled {
  color: var(--text-disabled);
  background: var(--instrument);
  border-color: var(--rule);
}

.btn--primary:disabled .btn__arrow {
  color: var(--text-disabled);
}

.btn--secondary {
  color: var(--accent);
  background: var(--paper-raised);
  border-color: var(--accent-soft);
}

.btn--secondary:hover:not(:disabled) {
  color: var(--ink);
  background: var(--paper-deep);
  border-color: var(--accent-strong);
  transform: translateY(-1px);
}

.btn--ghost {
  color: var(--text-muted);
  border-color: transparent;
}

.btn--ghost:hover:not(:disabled) {
  color: var(--ink);
  background: var(--paper-deep);
}

.btn--danger {
  color: var(--vermilion-strong);
  border-color: var(--vermilion);
  background: var(--vermilion-soft);
}

.btn--danger:hover:not(:disabled) {
  color: var(--vermilion-strong);
  border-color: var(--vermilion-strong);
  transform: translateY(-1px);
}

@media (hover: none) {
  .btn--primary:hover:not(:disabled),
  .btn--secondary:hover:not(:disabled),
  .btn--danger:hover:not(:disabled) {
    transform: none;
  }
}

.btn--sm {
  min-height: var(--control-md);
  padding: 0 12px;
  font-size: var(--type-label);
}

.btn--lg {
  min-height: 50px;
  padding: 0 24px;
  font-size: var(--type-body);
}

.btn--block {
  width: 100%;
}
</style>
