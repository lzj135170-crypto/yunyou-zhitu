<script setup>
import SectionHeader from '../ui/SectionHeader.vue'

defineProps({
  meta: { type: Object, default: () => ({}) },
  tripDayCount: { type: Number, default: 0 },
  hasBudget: { type: Boolean, default: false },
  budgetDisplay: { type: String, default: '—' },
  budgetCaption: { type: String, default: '' },
  perPersonBudget: { type: String, default: '—' },
  budgetAssumption: { type: String, default: '' },
})
</script>

<template>
  <section id="budget" class="result-section">
    <SectionHeader
      title="预算速览"
      description="目标预算与人均参考；费用明细尚未接入实时价格，具体以订单为准。"
    />

    <dl class="budget-ledger">
      <div class="budget-ledger__row budget-ledger__row--total">
        <dt>目标预算</dt>
        <dd class="num">{{ hasBudget ? `¥${budgetDisplay}` : '未设置' }}</dd>
      </div>
      <div class="budget-ledger__row">
        <dt>出行人数</dt>
        <dd class="num">{{ meta.travelers || '—' }} 人</dd>
      </div>
      <div class="budget-ledger__row">
        <dt>行程天数</dt>
        <dd class="num">{{ tripDayCount }} 天</dd>
      </div>
      <div class="budget-ledger__row">
        <dt>人均预算</dt>
        <dd class="num">{{ perPersonBudget }}</dd>
      </div>
      <div class="budget-ledger__row">
        <dt>费用明细</dt>
        <dd class="budget-ledger__muted">待接入实时价格</dd>
      </div>
    </dl>

    <p class="budget-caption">{{ budgetCaption }}</p>
    <p v-if="budgetAssumption" class="budget-assumption">{{ budgetAssumption }}</p>
  </section>
</template>

<style scoped>
.budget-ledger {
  margin-top: 18px;
  border-top: 1px solid var(--rule);
}

.budget-ledger__row {
  display: flex;
  gap: 24px;
  align-items: baseline;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid var(--rule);
}

.budget-ledger__row dt {
  color: var(--text-muted);
  font-size: var(--type-small);
}

.budget-ledger__row dd {
  margin: 0;
  color: var(--ink);
  font-size: var(--type-small);
  text-align: right;
}

.budget-ledger__row--total dt {
  color: var(--ink);
  font-weight: 600;
}

.budget-ledger__row--total dd {
  color: var(--accent);
  font-family: var(--font-display);
  font-size: 2.1rem;
  font-weight: 300;
  line-height: 1.05;
}

.budget-ledger__muted {
  color: var(--text-faint) !important;
}

.budget-caption {
  margin-top: 12px;
  color: var(--text-faint);
  font-size: var(--type-micro);
  line-height: 1.6;
}

.budget-assumption {
  margin-top: 12px;
  padding: 10px 12px;
  color: var(--text-muted);
  background: var(--paper-raised);
  border-left: 2px solid var(--accent-deep);
  font-size: var(--type-micro);
  line-height: 1.7;
}
</style>
