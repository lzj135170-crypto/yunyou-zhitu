<script setup>
defineProps({
  tone: { type: String, default: 'default' },
})
</script>

<template>
  <span class="tag" :class="`tag--${tone}`"><slot /></span>
</template>

<style scoped>
.tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 22px;
  padding: 0 7px;
  color: var(--text-muted);
  background: var(--paper-deep);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: var(--type-micro);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.04em;
  line-height: 1.35;
  overflow-wrap: anywhere;
  white-space: normal;
}

.tag--accent {
  color: var(--sun-deep);
  border-color: rgb(196 130 63 / 0.4);
  background: var(--sun-soft);
}

.tag--patina {
  color: var(--patina-deep);
  border-color: rgb(62 143 168 / 0.38);
  background: var(--patina-pale);
}

.tag--danger {
  color: var(--vermilion-strong);
  border-color: rgb(192 73 61 / 0.38);
  background: var(--vermilion-soft);
}
</style>
