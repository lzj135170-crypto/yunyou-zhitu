<script setup>
import { useId } from 'vue'

const props = defineProps({
  modelValue: { type: [String, Number], default: '' },
  label: { type: String, required: true },
  type: { type: String, default: 'text' },
  placeholder: { type: String, default: '' },
  autocomplete: { type: String, default: 'off' },
  min: { type: [Number, String], default: undefined },
  max: { type: [Number, String], default: undefined },
  hint: { type: String, default: '' },
  error: { type: String, default: '' },
  required: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  number: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue'])
const id = useId()

function onInput(event) {
  const raw = event.target.value
  emit('update:modelValue', props.number && raw !== '' ? Number(raw) : raw)
}
</script>

<template>
  <label class="field" :for="id">
    <span class="field__label">{{ label }}</span>
    <input
      :id="id"
      class="field__input"
      :type="type"
      :value="modelValue"
      :placeholder="placeholder"
      :autocomplete="autocomplete"
      :min="min"
      :max="max"
      :required="required"
      :disabled="disabled"
      :aria-invalid="Boolean(error)"
      :aria-describedby="error || hint ? `${id}-note` : undefined"
      @input="onInput"
    />
    <span v-if="error" :id="`${id}-note`" class="field__error" role="alert">{{ error }}</span>
    <span v-else-if="hint" :id="`${id}-note`" class="field__hint">{{ hint }}</span>
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

.field__input {
  width: 100%;
  min-height: var(--control-lg);
  padding: 0 12px;
  color: var(--ink);
  background: var(--paper-deep);
  border: 1px solid var(--edge);
  border-radius: var(--radius-sm);
  font-size: var(--type-body);
  outline: none;
  transition:
    border-color var(--quick) var(--ease),
    box-shadow var(--quick) var(--ease),
    background-color var(--quick) var(--ease);
}

.field__input:hover:not(:disabled) {
  border-color: var(--text-faint);
  background: var(--instrument-raised);
}

.field__input:focus {
  border-color: var(--patina-deep);
  background: var(--paper-raised);
  box-shadow: 0 0 0 3px rgb(42 115 136 / 0.16);
}

.field__input:focus-visible {
  outline: 2px solid var(--patina-deep);
  outline-offset: 2px;
}

.field__input:disabled {
  color: var(--text-disabled);
  background: var(--instrument-deep);
  cursor: not-allowed;
}

.field__hint {
  color: var(--text-faint);
  font-size: var(--type-micro);
  line-height: 1.5;
}

.field__error {
  color: var(--vermilion);
  font-size: var(--type-micro);
  line-height: 1.5;
}
</style>
