<script setup>
import { nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import AppButton from './AppButton.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, required: true },
  description: { type: String, default: '' },
  confirmText: { type: String, default: '确认' },
  cancelText: { type: String, default: '取消' },
  danger: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['confirm', 'cancel'])
const dialogRef = ref(null)
const cancelRef = ref(null)
const titleId = useId()
const descriptionId = useId()
let lastActive = null

watch(
  () => props.open,
  async (value) => {
    await nextTick()
    const dialog = dialogRef.value
    if (!dialog) return

    if (value && !dialog.open) {
      lastActive = document.activeElement
      dialog.showModal()
      cancelRef.value?.$el?.focus()
    } else if (!value && dialog.open) {
      dialog.close()
    }
  }
)

function onCancel(event) {
  event.preventDefault()
  if (props.busy) return
  emit('cancel')
}

function onClose() {
  if (lastActive && typeof lastActive.focus === 'function') lastActive.focus()
  lastActive = null
}

onBeforeUnmount(() => {
  if (dialogRef.value?.open) dialogRef.value.close()
})
</script>

<template>
  <dialog
    ref="dialogRef"
    class="dialog"
    :aria-labelledby="titleId"
    :aria-describedby="description ? descriptionId : undefined"
    @cancel="onCancel"
    @close="onClose"
  >
    <div class="dialog__body">
      <h2 :id="titleId">{{ title }}</h2>
      <p v-if="description" :id="descriptionId" class="dialog__description">{{ description }}</p>
      <slot />
      <div class="dialog__actions">
        <AppButton ref="cancelRef" variant="secondary" :disabled="busy" @click="emit('cancel')">
          {{ cancelText }}
        </AppButton>
        <AppButton :variant="danger ? 'danger' : 'primary'" :loading="busy" @click="emit('confirm')">
          {{ confirmText }}
        </AppButton>
      </div>
    </div>
  </dialog>
</template>

<style scoped>
.dialog {
  width: min(440px, calc(100vw - 32px));
  padding: 0;
  color: var(--text);
  background: var(--paper-raised);
  border: 1px solid var(--rule-strong);
  border-radius: var(--radius-md);
  box-shadow: var(--lift-dialog);
}

.dialog::backdrop {
  background: var(--scrim);
  backdrop-filter: blur(2px);
}

.dialog__body {
  padding: 24px;
}

.dialog__body h2 {
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: 1.15rem;
  font-weight: 650;
  line-height: 1.35;
}

.dialog__description {
  margin-top: 8px;
  color: var(--text-muted);
  font-size: var(--type-small);
  line-height: 1.7;
}

.dialog__actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  margin-top: 24px;
}
</style>
