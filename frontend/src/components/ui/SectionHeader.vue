<script setup>
defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' },
})
</script>

<template>
  <header class="section-head">
    <div>
      <h2>{{ title }}</h2>
      <p v-if="description">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="section-head__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.section-head {
  display: flex;
  gap: 20px;
  align-items: flex-end;
  justify-content: space-between;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--rule-strong);
}

.section-head h2 {
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: 1.2rem;
  font-weight: 650;
  line-height: 1.3;
}

.section-head p {
  max-width: 62ch;
  margin-top: 4px;
  color: var(--text-muted);
  font-size: var(--type-small);
}

.section-head__actions {
  display: flex;
  flex: none;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: flex-end;
}

@media (max-width: 640px) {
  .section-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .section-head__actions {
    width: 100%;
    justify-content: flex-start;
  }
}
</style>
