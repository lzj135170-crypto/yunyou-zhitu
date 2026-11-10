<script setup>
import AppButton from '../components/ui/AppButton.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import Icon from '../components/ui/Icon.vue'
import PageHeader from '../components/layout/PageHeader.vue'
import { formatDateTime, parseDateOnly } from '../utils/date'

const props = defineProps({
  conversations: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  metaFor: { type: Function, required: true },
  subtitleFor: { type: Function, required: true },
})

const emit = defineEmits(['refresh', 'open', 'delete', 'plan'])

function metaOf(conv) {
  return props.metaFor(conv) || {}
}

function monogramOf(conv) {
  const destination = metaOf(conv).destination || conv.title || '旅'
  return destination.trim().slice(0, 1).toUpperCase() || '旅'
}

function daysOf(conv) {
  const meta = metaOf(conv)
  const start = parseDateOnly(meta.startDate)
  const end = parseDateOnly(meta.endDate)
  if (start && end && end >= start) {
    return Math.round((end.getTime() - start.getTime()) / 86400000) + 1
  }
  const fromTitle = String(conv.title || '').match(/·\s*(\d+)\s*天/)
  return fromTitle ? Number(fromTitle[1]) : null
}

function budgetOf(conv) {
  const value = Number(metaOf(conv).budget)
  return Number.isFinite(value) && value > 0 ? `¥${value.toLocaleString('zh-CN')}` : '未记录'
}
</script>

<template>
  <section class="history-page">
    <PageHeader
      title="历史行程"
      description="每次规划都会留下一条旅行日志，点开可以继续调整；删除后聊天记录和行程数据一并移除。"
    >
      <template #actions>
        <AppButton variant="secondary" :loading="loading" @click="emit('refresh')">
          <Icon name="refresh" :size="16" />
          刷新列表
        </AppButton>
      </template>
    </PageHeader>

    <div v-if="conversations.length" class="history-grid">
      <article
        v-for="(conv, index) in conversations"
        :key="conv.id"
        class="history-row history-card"
      >
        <div class="history-card__rail" aria-hidden="true">
          <span class="history-card__monogram">{{ monogramOf(conv) }}</span>
          <span class="history-card__line"></span>
        </div>

        <div class="history-card__body">
          <header class="history-card__head">
            <p class="mono">TRAVEL LOG / {{ String(index + 1).padStart(2, '0') }}</p>
            <span class="history-card__time num">
              {{ formatDateTime(conv.update_time) || '未记录时间' }}
            </span>
          </header>

          <div class="history-card__place">
            <h2>{{ metaOf(conv).destination || conv.title || '未记录目的地' }}</h2>
            <h3 v-if="metaOf(conv).destination" class="history-card__title">
              {{ conv.title || '未命名行程' }}
            </h3>
          </div>

          <p
            class="history-card__subtitle"
            :class="{ 'history-card__subtitle--lead': !metaOf(conv).destination }"
          >
            {{ subtitleFor(conv) }}
          </p>

          <dl class="history-card__facts">
            <div>
              <dt>天数</dt>
              <dd class="num">{{ daysOf(conv) ? `${daysOf(conv)} 天` : '未记录' }}</dd>
            </div>
            <div>
              <dt>人数</dt>
              <dd class="num">
                {{ metaOf(conv).travelers ? `${metaOf(conv).travelers} 人` : '未记录' }}
              </dd>
            </div>
            <div>
              <dt>预算</dt>
              <dd class="num">{{ budgetOf(conv) }}</dd>
            </div>
          </dl>

          <footer class="history-card__actions">
            <AppButton variant="secondary" size="sm" @click="emit('open', conv)">
              查看详情
            </AppButton>
            <button
              class="history-card__delete history-row__delete"
              type="button"
              @click="emit('delete', conv)"
            >
              <Icon name="trash" :size="14" />
              删除
            </button>
          </footer>
        </div>
      </article>
    </div>

    <EmptyState
      v-else
      icon="compass"
      title="还没有历史行程"
      description="去规划页填一份旅行简报，生成后会自动留在这里。"
    >
      <AppButton variant="primary" arrow @click="emit('plan')">开始规划</AppButton>
    </EmptyState>
  </section>
</template>

<style scoped>
.history-page {
  padding-top: clamp(28px, 4vw, 48px);
}

.history-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(320px, 100%), 1fr));
  gap: 16px;
  margin-top: 28px;
}

.history-card {
  display: grid;
  grid-template-columns: 66px minmax(0, 1fr);
  gap: 16px;
  min-width: 0;
  padding: 16px;
  background: var(--paper-raised);
  border: 1px solid var(--rule);
  border-radius: var(--radius-md);
  transition:
    border-color var(--dur-2) var(--ease-out),
    transform var(--dur-2) var(--ease-out),
    box-shadow var(--dur-2) var(--ease-out);
}

.history-card__rail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: center;
  min-height: 100%;
}

.history-card__monogram {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 58px;
  height: 58px;
  color: var(--accent-deep);
  background:
    linear-gradient(135deg, var(--instrument), var(--paper-deep));
  border: 1px solid rgb(31 111 92 / 0.28);
  border-radius: var(--radius-sm);
  font-family: var(--font-display);
  font-size: 1.85rem;
  font-weight: 300;
  line-height: 1;
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 0.65);
}

.history-card__line {
  flex: 1;
  width: 1px;
  min-height: 20px;
  background: linear-gradient(var(--rule-strong), transparent);
}

.history-card__body {
  min-width: 0;
}

.history-card__head {
  display: flex;
  gap: 12px;
  align-items: baseline;
  justify-content: space-between;
}

.history-card__head .mono {
  color: var(--text-faint);
  font-size: 0.58rem;
  letter-spacing: 0.15em;
}

.history-card__place {
  min-width: 0;
  margin-top: 8px;
}

.history-card__place h2 {
  color: var(--ink);
  font-family: var(--font-display);
  font-size: 1.65rem;
  font-weight: 400;
  line-height: 1.1;
  overflow-wrap: anywhere;
}

.history-card__time {
  flex: none;
  color: var(--text-faint);
  font-family: var(--font-mono);
  font-size: var(--type-micro);
  line-height: 1.5;
  text-align: right;
}

.history-card__title {
  margin-top: 4px;
  color: var(--text);
  font-family: var(--font-ui);
  font-size: var(--type-small);
  font-weight: 600;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.history-card__subtitle {
  margin-top: 6px;
  color: var(--text-muted);
  font-size: var(--type-label);
  line-height: 1.6;
}

.history-card__subtitle--lead {
  margin-top: 10px;
}

.history-card__facts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  justify-content: space-between;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--rule);
}

.history-card__facts > div {
  display: flex;
  gap: 6px;
  align-items: baseline;
}

.history-card__facts dt {
  color: var(--text-faint);
  font-size: var(--type-micro);
}

.history-card__facts dd {
  margin: 0;
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: var(--type-label);
}

.history-card__actions {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  margin-top: 16px;
}

.history-card__delete {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  padding: 5px 7px;
  color: var(--text-muted);
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  font-size: var(--type-label);
  transition:
    color var(--quick) var(--ease),
    background-color var(--quick) var(--ease);
}

.history-card__delete:hover {
  color: var(--vermilion);
  background: var(--vermilion-soft);
}

@media (hover: hover) {
  .history-card:hover {
    border-color: var(--rule-strong);
    box-shadow: var(--lift-1);
    transform: translateY(-2px);
  }
}

@media (max-width: 560px) {
  .history-card {
    grid-template-columns: 44px minmax(0, 1fr);
    gap: 13px;
    padding: 15px;
  }

  .history-card__monogram {
    width: 42px;
    height: 42px;
    font-size: 1.4rem;
  }

  .history-card__place h2 {
    font-size: 1.4rem;
  }
}
</style>
