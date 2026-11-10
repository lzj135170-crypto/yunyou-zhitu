<script setup>
import CountUp from './CountUp.vue'

defineProps({
  items: { type: Array, default: () => [] },
})
</script>

<template>
  <dl v-if="items.length" class="stat-bar" aria-label="行程关键信息">
    <div v-for="item in items" :key="item.label" class="stat-bar__item">
      <dt>{{ item.label }}</dt>
      <dd>
        <span v-if="Number.isFinite(item.count)" class="stat-bar__value num">
          <CountUp
            :value="item.count"
            :prefix="item.countPrefix || ''"
            :suffix="item.suffix || ''"
            :label="item.ariaLabel || `${item.value}${item.suffix || ''}`"
          />
        </span>
        <template v-else>
          <span class="stat-bar__value num">{{ item.value }}</span>
          <span v-if="item.suffix" class="stat-bar__suffix">{{ item.suffix }}</span>
        </template>
      </dd>
    </div>
  </dl>
</template>

<style scoped>
.stat-bar {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  margin: 0;
  padding: 16px 18px;
  background: var(--paper-raised);
  border: 1px solid var(--rule);
  border-radius: var(--radius-md);
  box-shadow: 0 1px 2px rgb(31 51 45 / 0.04);
}

.stat-bar__item {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
  padding: 0 14px;
  border-left: 1px solid var(--rule);
}

.stat-bar__item:first-child {
  padding-left: 0;
  border-left: 0;
}

.stat-bar__item dt {
  color: var(--text-faint);
  font-family: var(--font-mono);
  font-size: var(--type-micro);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  white-space: nowrap;
}

.stat-bar__item dd {
  display: flex;
  gap: 3px;
  align-items: baseline;
  min-width: 0;
  margin: 0;
  color: var(--ink);
  white-space: nowrap;
}

.stat-bar__value {
  overflow: hidden;
  font-family: var(--font-display);
  font-size: 1.35rem;
  font-weight: 400;
  line-height: 1.1;
  text-overflow: ellipsis;
}

.stat-bar__suffix {
  color: var(--text-muted);
  font-family: var(--font-ui);
  font-size: var(--type-micro);
}

@media (max-width: 1180px) {
  .stat-bar {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .stat-bar__item:nth-child(3n + 1) {
    padding-left: 0;
    border-left: 0;
  }

  .stat-bar__item:nth-child(n + 4) {
    margin-top: 14px;
    padding-top: 14px;
    border-top: 1px solid var(--rule);
  }
}

@media (max-width: 600px) {
  .stat-bar {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .stat-bar__item:nth-child(n) {
    padding-left: 14px;
    border-left: 1px solid var(--rule);
  }

  .stat-bar__item:nth-child(2n + 1) {
    padding-left: 0;
    border-left: 0;
  }

  .stat-bar__item:nth-child(n + 3) {
    margin-top: 14px;
    padding-top: 14px;
    border-top: 1px solid var(--rule);
  }

  .stat-bar__item:nth-child(-n + 2) {
    margin-top: 0;
    padding-top: 0;
    border-top: 0;
  }
}
</style>
