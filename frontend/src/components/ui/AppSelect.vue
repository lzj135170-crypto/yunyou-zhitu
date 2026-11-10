<script setup>
import { useId } from 'vue'

defineProps({
  modelValue: { type: [String, Number], default: '' },
  label: { type: String, required: true },
  options: { type: Array, required: true },
  hint: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue'])
const id = useId()
</script>

<template>
  <label class="field" :for="id">
    <span class="field__label">{{ label }}</span>
    <span class="select-wrap">
      <select
        :id="id"
        class="field__select"
        :value="modelValue"
        :disabled="disabled"
        :aria-describedby="hint ? `${id}-hint` : undefined"
        @change="emit('update:modelValue', $event.target.value)"
      >
        <option v-for="option in options" :key="option.value ?? option" :value="option.value ?? option">
          {{ option.label ?? option }}
        </option>
      </select>
    </span>
    <span v-if="hint" :id="`${id}-hint`" class="field__hint">{{ hint }}</span>
  </label>
</template>

<style scoped>
.field {
  display: flex;
  flex-direction: column;
  gap: 7px;
  min-width: 0;
}

.field__label {
  color: var(--text-muted);
  font-size: var(--type-label);
  font-weight: 550;
  letter-spacing: 0.02em;
}

.select-wrap {
  position: relative;
  display: block;
}

.select-wrap::after {
  position: absolute;
  top: 50%;
  right: 13px;
  width: 7px;
  height: 7px;
  border-right: 1.5px solid var(--text-muted);
  border-bottom: 1.5px solid var(--text-muted);
  content: "";
  pointer-events: none;
  transform: translateY(-70%) rotate(45deg);
}

.field__select {
  width: 100%;
  min-height: var(--control-lg);
  padding: 0 36px 0 12px;
  color: var(--ink);
  background: var(--paper-deep);
  border: 1px solid var(--edge);
  border-radius: var(--radius-sm);
  font-size: var(--type-body);
  appearance: none;
  cursor: pointer;
  transition:
    border-color var(--quick) var(--ease),
    background-color var(--quick) var(--ease),
    box-shadow var(--quick) var(--ease);
}

.field__select:hover:not(:disabled) {
  border-color: var(--text-faint);
  background: var(--instrument-raised);
}

.field__select:focus {
  border-color: var(--patina-deep);
  outline: none;
  background: var(--paper-raised);
  box-shadow: 0 0 0 3px rgb(42 115 136 / 0.16);
}

.field__select:focus-visible {
  outline: 2px solid var(--patina-deep);
  outline-offset: 2px;
}

.field__select:disabled {
  color: var(--text-disabled);
  cursor: not-allowed;
}

.field__hint {
  color: var(--text-faint);
  font-size: var(--type-micro);
}
</style>
