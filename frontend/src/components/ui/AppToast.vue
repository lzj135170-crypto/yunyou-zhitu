<script setup>
import { computed } from 'vue'
import Icon from './Icon.vue'
import { useToast } from '../../composables/useToast'

const { toast, clearToast } = useToast()

const iconName = computed(() => {
  if (!toast.value) return 'info'
  if (toast.value.type === 'error') return 'alert'
  if (toast.value.type === 'success') return 'check'
  return 'info'
})
</script>

<template>
  <Teleport to="body">
    <Transition name="toast">
      <div
        v-if="toast"
        class="toast"
        :class="`toast--${toast.type}`"
        :role="toast.type === 'error' ? 'alert' : 'status'"
        :aria-live="toast.type === 'error' ? 'assertive' : 'polite'"
      >
        <Icon :name="iconName" :size="17" class="toast__icon" />
        <span class="toast__message">{{ toast.message }}</span>
        <button class="toast__close" type="button" aria-label="关闭提示" @click="clearToast">
          <Icon name="x" :size="14" />
        </button>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.toast {
  position: fixed;
  top: 18px;
  left: 50%;
  z-index: 90;
  display: flex;
  gap: 10px;
  align-items: flex-start;
  width: min(520px, calc(100vw - 32px));
  padding: 12px 12px 12px 14px;
  color: var(--text);
  background: var(--paper-raised);
  border: 1px solid var(--rule-strong);
  border-radius: var(--radius-sm);
  box-shadow: var(--lift-1);
  transform: translateX(-50%);
}

.toast--error {
  border-color: rgb(192 73 61 / 0.45);
}

.toast--success {
  border-color: rgb(94 158 139 / 0.55);
}

.toast__icon {
  margin-top: 1px;
  color: var(--patina-deep);
}

.toast--success .toast__icon {
  color: var(--accent);
}

.toast--error .toast__icon {
  color: var(--vermilion);
}

.toast__message {
  flex: 1;
  font-size: var(--type-small);
  line-height: 1.6;
}

.toast__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  margin-top: -2px;
  color: var(--text-muted);
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
}

.toast__close:hover {
  color: var(--ink);
  background: var(--paper-deep);
}

.toast-enter-active,
.toast-leave-active {
  transition:
    opacity var(--settle) var(--ease),
    transform var(--settle) var(--ease);
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translate(-50%, -8px);
}
</style>
