<script setup>
import EmptyState from '../ui/EmptyState.vue'
import Icon from '../ui/Icon.vue'
import SectionHeader from '../ui/SectionHeader.vue'
import { transportLabel } from '../../utils/labels'

defineProps({
  transport: { type: Array, default: () => [] },
})

function transportIcon(mode) {
  const key = String(mode || '').toLowerCase()
  if (key.includes('airport')) return 'train'
  if (key.includes('plane') || key.includes('flight') || key.includes('air')) return 'plane'
  if (key.includes('bus') || key.includes('coach')) return 'bus'
  if (key.includes('train') || key.includes('rail') || key.includes('metro') || key.includes('subway')) {
    return 'train'
  }
  if (
    key.includes('self') ||
    key.includes('drive') ||
    key.includes('car') ||
    key.includes('taxi') ||
    key.includes('cab')
  ) {
    return 'car'
  }
  return 'route'
}
</script>

<template>
  <section id="transport" class="result-section">
    <SectionHeader
      title="交通方案"
      description="到达目的地后的移动方式；耗时与票价是区间，具体班次以官方为准。"
    />

    <div v-if="transport.length" class="transport-list">
      <article v-for="item in transport" :key="item.mode" class="transport-row">
        <header class="transport-row__head">
          <span class="transport-row__icon" aria-hidden="true">
            <Icon :name="transportIcon(item.mode)" :size="18" />
          </span>
          <div class="transport-row__title">
            <h3>{{ transportLabel(item.mode) }}</h3>
            <p v-if="item.note">{{ item.note }}</p>
          </div>
        </header>

        <dl class="transport-row__facts">
          <div>
            <dt>耗时</dt>
            <dd class="num">{{ item.duration || '—' }}</dd>
          </div>
          <div>
            <dt>票价</dt>
            <dd class="num">{{ item.price_range || '—' }}</dd>
          </div>
        </dl>

        <p v-if="item.best_for" class="transport-row__best">
          <span class="mono">适合</span>
          {{ item.best_for }}
        </p>
      </article>
    </div>

    <EmptyState
      v-else
      icon="train"
      title="这次没有交通方案"
      description="可以回到规划页补充出发地和偏好，或在底部继续调整行程。"
    />
  </section>
</template>

<style scoped>
.transport-list {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 8px;
  margin-top: 18px;
}

.transport-row {
  padding: 13px 14px;
  background: var(--paper-raised);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
}

.transport-row__head {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.transport-row__icon {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  color: var(--accent);
  background: var(--paper-deep);
  border: 1px solid var(--rule);
  border-radius: 50%;
}

.transport-row__title {
  min-width: 0;
}

.transport-row__title h3 {
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: var(--type-small);
  font-weight: 650;
  line-height: 1.4;
}

.transport-row__title p {
  margin-top: 2px;
  color: var(--text-muted);
  font-size: var(--type-micro);
  line-height: 1.6;
}

.transport-row__facts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 22px;
  margin-top: 10px;
  padding-top: 9px;
  border-top: 1px solid var(--rule);
}

.transport-row__facts > div {
  display: flex;
  gap: 7px;
  align-items: baseline;
}

.transport-row__facts dt {
  color: var(--text-faint);
  font-size: var(--type-micro);
}

.transport-row__facts dd {
  margin: 0;
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: var(--type-label);
}

.transport-row__best {
  margin-top: 8px;
  color: var(--text-muted);
  font-size: var(--type-label);
  line-height: 1.65;
}

.transport-row__best .mono {
  margin-right: 5px;
  color: var(--text-faint);
  font-size: 0.58rem;
}

@media (max-width: 640px) {
  .transport-row__facts {
    gap: 6px 16px;
  }
}
</style>
