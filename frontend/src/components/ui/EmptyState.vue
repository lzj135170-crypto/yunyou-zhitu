<script setup>
import Icon from './Icon.vue'

defineProps({
  icon: { type: String, default: 'compass' },
  title: { type: String, required: true },
  description: { type: String, default: '' },
})
</script>

<template>
  <div class="empty-state">
    <Icon :name="icon" :size="28" class="empty-state__icon" />
    <h3>{{ title }}</h3>
    <p v-if="description">{{ description }}</p>
    <div v-if="$slots.default" class="empty-state__action">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 40px 24px;
  text-align: center;
  background: var(--paper-deep);
  border: 1px solid var(--rule);
  border-radius: var(--radius-md);
}

.empty-state__icon {
  margin-bottom: 12px;
  color: var(--text-faint);
}

.empty-state h3 {
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: 1.05rem;
  font-weight: 650;
}

.empty-state p {
  max-width: 46ch;
  margin-top: 6px;
  color: var(--text-muted);
  font-size: var(--type-small);
}

.empty-state__action {
  margin-top: 18px;
}
</style>
