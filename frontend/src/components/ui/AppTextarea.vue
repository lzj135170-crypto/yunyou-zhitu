<script setup>
import { useId } from 'vue'

defineProps({
  modelValue: { type: String, default: '' },
  label: { type: String, required: true },
  rows: { type: [Number, String], default: 5 },
  placeholder: { type: String, default: '' },
  hint: { type: String, default: '' },
  maxlength: { type: [Number, String], default: undefined },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue'])
const id = useId()
</script>

<template>
  <label class="field" :for="id">
    <span class="field__label">{{ label }}</span>
    <textarea
      :id="id"
      class="field__textarea"
      :value="modelValue"
      :rows="rows"
      :placeholder="placeholder"
      :maxlength="maxlength"
      :disabled="disabled"
      :aria-describedby="hint ? `${id}-hint` : undefined"
      @input="emit('update:modelValue', $event.target.value)"
    ></textarea>
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

.field__textarea {
  width: 100%;
  min-height: 112px;
  padding: 11px 12px;
  color: var(--ink);
  background: var(--paper-deep);
  border: 1px solid var(--edge);
  border-radius: var(--radius-sm);
  font-size: var(--type-body);
  line-height: 1.7;
  resize: vertical;
  transition:
    border-color var(--quick) var(--ease),
    background-color var(--quick) var(--ease),
    box-shadow var(--quick) var(--ease);
}

.field__textarea:hover:not(:disabled) {
  border-color: var(--text-faint);
  background: var(--instrument-raised);
}

.field__textarea:focus {
  border-color: var(--patina-deep);
  outline: none;
  background: var(--paper-raised);
  box-shadow: 0 0 0 3px rgb(42 115 136 / 0.16);
}

.field__textarea:focus-visible {
  outline: 2px solid var(--patina-deep);
  outline-offset: 2px;
}

.field__hint {
  color: var(--text-faint);
  font-size: var(--type-micro);
}
</style>
