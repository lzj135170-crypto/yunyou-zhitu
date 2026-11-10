<script setup>
import { computed } from 'vue'
import AppButton from '../components/ui/AppButton.vue'
import AppChip from '../components/ui/AppChip.vue'
import AppInput from '../components/ui/AppInput.vue'
import AppSelect from '../components/ui/AppSelect.vue'
import AppTextarea from '../components/ui/AppTextarea.vue'
import PageHeader from '../components/layout/PageHeader.vue'
import { parseDateOnly } from '../utils/date'

const props = defineProps({
  planForm: { type: Object, required: true },
  preferenceOptions: { type: Array, default: () => [] },
  dietaryOptions: { type: Array, default: () => [] },
  dayCount: { type: Number, default: 0 },
  creatingTrip: { type: Boolean, default: false },
})

const emit = defineEmits(['toggle-preference', 'submit'])

const completeness = computed(() => {
  const form = props.planForm
  const start = parseDateOnly(form.startDate)
  const end = parseDateOnly(form.endDate)
  const items = [
    { label: '目的地', done: Boolean(form.destination.trim()) },
    { label: '日期', done: Boolean(start && end && end >= start) },
    { label: '人数', done: Number(form.travelers) >= 1 },
    { label: '预算', done: Number(form.budget) > 0 },
    {
      label: '偏好',
      done: form.preferences.length > 0 || form.dietary.length > 0,
    },
    { label: '备注', done: Boolean(form.notes.trim()) },
  ]
  const done = items.filter((item) => item.done).length
  return {
    done,
    total: items.length,
    percent: Math.round((done / items.length) * 100),
    missing: items.filter((item) => !item.done).map((item) => item.label),
  }
})

const paceOptions = [
  { value: '轻松', label: '轻松' },
  { value: '适中', label: '适中' },
  { value: '紧凑', label: '紧凑' },
]

const hotelOptions = [
  { value: '舒适型', label: '舒适型' },
  { value: '高档型', label: '高档型' },
  { value: '经济型', label: '经济型' },
]
</script>

<template>
  <section class="plan-page">
    <PageHeader
      title="规划下一段旅程"
      description="填得越具体，行程越贴近你的节奏。目的地、日期和偏好会一起交给行程编排。"
    />

    <div class="plan-layout">
      <div class="plan-sheet">
        <section class="plan-block">
          <div class="plan-block__head">
            <div>
              <h2>目的地与日期</h2>
              <p>先定下这趟旅行的骨架。</p>
            </div>
            <span class="plan-block__index mono" aria-hidden="true">01</span>
          </div>

          <div class="plan-grid plan-grid--4">
            <div class="plan-span-2">
              <AppInput
                v-model="planForm.destination"
                label="目的地城市"
                placeholder="例如：成都、大理、北京"
              />
            </div>
            <AppInput v-model="planForm.startDate" label="开始日期" type="date" />
            <AppInput v-model="planForm.endDate" label="结束日期" type="date" />
            <AppInput
              v-model="planForm.travelers"
              label="人数"
              type="number"
              :min="1"
              :max="99"
              number
            />
            <div class="plan-info">
              <span class="plan-info__label">旅行天数</span>
              <span class="plan-info__value num">{{ dayCount }} 天</span>
            </div>
          </div>
        </section>

        <section class="plan-block">
          <div class="plan-block__head">
            <div>
              <h2>偏好设置</h2>
              <p>告诉行程你的节奏、住宿和预算。</p>
            </div>
            <span class="plan-block__index mono" aria-hidden="true">02</span>
          </div>

          <div class="plan-grid plan-grid--3">
            <AppSelect
              :model-value="planForm.pace"
              label="节奏偏好"
              :options="paceOptions"
              @update:model-value="planForm.pace = $event"
            />
            <AppSelect
              :model-value="planForm.hotelLevel"
              label="住宿偏好"
              :options="hotelOptions"
              @update:model-value="planForm.hotelLevel = $event"
            />
            <AppInput
              v-model="planForm.budget"
              label="预算（元）"
              type="number"
              :min="0"
              placeholder="例如 3200"
              number
            />
          </div>

          <div class="plan-chips">
            <span class="plan-chips__label">旅行偏好</span>
            <div class="plan-chips__list">
              <AppChip
                v-for="option in preferenceOptions"
                :key="option"
                :active="planForm.preferences.includes(option)"
                @click="emit('toggle-preference', 'preferences', option)"
              >
                {{ option }}
              </AppChip>
            </div>
          </div>

          <div class="plan-chips">
            <span class="plan-chips__label">饮食偏好</span>
            <div class="plan-chips__list">
              <AppChip
                v-for="option in dietaryOptions"
                :key="option"
                :active="planForm.dietary.includes(option)"
                @click="emit('toggle-preference', 'dietary', option)"
              >
                {{ option }}
              </AppChip>
            </div>
          </div>
        </section>

        <section class="plan-block">
          <div class="plan-block__head">
            <div>
              <h2>额外要求</h2>
              <p>不想太早起、想看日落、带老人小孩……都可以写。</p>
            </div>
            <span class="plan-block__index mono" aria-hidden="true">03</span>
          </div>

          <AppTextarea
            v-model="planForm.notes"
            label="行程备注"
            :rows="6"
            placeholder="例如：不想太早起床，希望安排一个适合看日落的地点。"
            :hint="`共 ${planForm.notes.length} 字`"
          />
        </section>
      </div>

      <aside class="plan-summary">
        <p class="plan-summary__label mono">本次行程</p>
        <p class="plan-summary__destination">
          {{ planForm.destination.trim() || '未填写目的地' }}
        </p>

        <div
          class="plan-summary__meter"
          :style="{ '--brief-progress': `${completeness.percent}%` }"
        >
          <div class="plan-summary__meter-head">
            <span class="mono">简报完整度</span>
            <span class="num">{{ completeness.done }}/{{ completeness.total }}</span>
          </div>
          <div
            class="plan-summary__meter-track"
            role="progressbar"
            aria-label="旅行简报完整度"
            :aria-valuenow="completeness.percent"
            aria-valuemin="0"
            aria-valuemax="100"
          >
            <span class="plan-summary__meter-fill"></span>
          </div>
          <p
            v-if="completeness.missing.length"
            class="plan-summary__meter-note"
          >
            还差：{{ completeness.missing.join('、') }}
          </p>
          <p v-else class="plan-summary__meter-note plan-summary__meter-note--ready">
            信息已齐，可以开始规划。
          </p>
        </div>

        <dl class="plan-summary__facts">
          <div>
            <dt>日期</dt>
            <dd class="num">{{ planForm.startDate || '—' }} 至 {{ planForm.endDate || '—' }}</dd>
          </div>
          <div>
            <dt>天数</dt>
            <dd class="num">{{ dayCount }} 天</dd>
          </div>
          <div>
            <dt>人数</dt>
            <dd class="num">{{ planForm.travelers || '—' }} 人</dd>
          </div>
          <div>
            <dt>预算</dt>
            <dd class="num">
              {{ Number(planForm.budget) > 0 ? `¥${Number(planForm.budget).toLocaleString('zh-CN')}` : '未设置' }}
            </dd>
          </div>
          <div>
            <dt>节奏</dt>
            <dd>{{ planForm.pace }}</dd>
          </div>
          <div>
            <dt>住宿</dt>
            <dd>{{ planForm.hotelLevel }}</dd>
          </div>
        </dl>

        <div v-if="planForm.preferences.length || planForm.dietary.length" class="plan-summary__tags">
          <span v-for="item in planForm.preferences" :key="item">{{ item }}</span>
          <span v-for="item in planForm.dietary" :key="item">{{ item }}</span>
        </div>

        <AppButton
          variant="primary"
          size="lg"
          block
          arrow
          :loading="creatingTrip"
          @click="emit('submit')"
        >
          {{ creatingTrip ? '正在生成行程…' : '开始规划' }}
        </AppButton>
        <p class="plan-summary__note">
          会依次调用景点、住宿、交通、天气等智能体，复杂行程可能需要 1–3 分钟。
        </p>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.plan-page {
  padding-top: clamp(28px, 4vw, 48px);
}

.plan-layout {
  display: grid;
  grid-template-columns: minmax(0, 8fr) minmax(280px, 4fr);
  gap: clamp(28px, 5vw, 64px);
  align-items: start;
  margin-top: 28px;
}

.plan-sheet {
  border-top: 1px solid var(--rule-strong);
}

.plan-block {
  padding: 26px 0 30px;
  border-bottom: 1px solid var(--rule);
}

.plan-block__head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 18px;
}

.plan-block__index {
  flex: none;
  padding-top: 3px;
  color: var(--accent-deep);
  font-size: 0.6rem;
  letter-spacing: 0.16em;
}

.plan-block__head h2 {
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: 1.1rem;
  font-weight: 650;
  line-height: 1.3;
}

.plan-block__head p {
  margin-top: 3px;
  color: var(--text-muted);
  font-size: var(--type-small);
}

.plan-grid {
  display: grid;
  gap: 16px;
}

.plan-grid--4 {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.plan-grid--3 {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.plan-span-2 {
  grid-column: span 2;
}

.plan-info {
  display: flex;
  flex-direction: column;
  gap: 7px;
  justify-content: flex-end;
}

.plan-info__label {
  color: var(--text-muted);
  font-size: var(--type-label);
  font-weight: 550;
}

.plan-info__value {
  display: flex;
  align-items: center;
  min-height: var(--control-lg);
  padding: 0 12px;
  color: var(--ink);
  background: var(--paper-deep);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: var(--type-small);
}

.plan-chips {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  margin-top: 20px;
}

.plan-chips__label {
  flex: none;
  width: 74px;
  padding-top: 8px;
  color: var(--text-muted);
  font-size: var(--type-label);
  font-weight: 550;
}

.plan-chips__list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.plan-summary {
  position: sticky;
  top: 96px;
  padding: 20px;
  background: var(--paper-raised);
  border: 1px solid var(--rule-strong);
  border-radius: var(--radius-md);
  box-shadow: var(--lift-1);
}

.plan-summary::before {
  position: absolute;
  top: -1px;
  right: 26px;
  left: 26px;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  content: "";
  opacity: 0.72;
}

.plan-summary__label {
  color: var(--text-faint);
}

.plan-summary__destination {
  margin-top: 8px;
  color: var(--ink);
  font-family: var(--font-display);
  font-size: 2.05rem;
  font-weight: 300;
  line-height: 1.1;
  overflow-wrap: anywhere;
}

.plan-summary__meter {
  margin-top: 16px;
}

.plan-summary__meter-head {
  display: flex;
  gap: 12px;
  align-items: baseline;
  justify-content: space-between;
  color: var(--text-faint);
  font-size: var(--type-micro);
}

.plan-summary__meter-head .num {
  color: var(--accent);
  font-family: var(--font-mono);
  font-size: var(--type-label);
}

.plan-summary__meter-track {
  position: relative;
  height: 2px;
  margin-top: 8px;
  overflow: hidden;
  background: var(--rule);
}

.plan-summary__meter-fill {
  position: absolute;
  inset: 0 auto 0 0;
  width: var(--brief-progress);
  background: linear-gradient(90deg, var(--accent-deep), var(--accent));
  box-shadow: 0 0 12px rgb(31 111 92 / 0.22);
  transition: width var(--dur-3) var(--ease-out);
}

.plan-summary__meter-note {
  margin-top: 7px;
  color: var(--text-faint);
  font-size: var(--type-micro);
  line-height: 1.55;
}

.plan-summary__meter-note--ready {
  color: var(--patina-deep);
}

.plan-summary__facts {
  margin-top: 16px;
  border-top: 1px solid var(--rule);
}

.plan-summary__facts > div {
  display: flex;
  gap: 12px;
  align-items: baseline;
  justify-content: space-between;
  padding: 7px 0;
  border-bottom: 1px solid var(--rule);
}

.plan-summary__facts dt {
  color: var(--text-muted);
  font-size: var(--type-label);
}

.plan-summary__facts dd {
  margin: 0;
  color: var(--ink);
  font-size: var(--type-small);
  text-align: right;
}

.plan-summary__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin: 14px 0 18px;
}

.plan-summary__tags span {
  padding: 3px 7px;
  color: var(--text-muted);
  background: var(--paper-deep);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
  font-size: var(--type-micro);
}

.plan-summary__note {
  margin-top: 12px;
  color: var(--text-faint);
  font-size: var(--type-micro);
  line-height: 1.6;
}

@media (max-width: 1080px) {
  .plan-layout {
    grid-template-columns: minmax(0, 1fr) minmax(250px, 0.55fr);
    gap: 32px;
  }

  .plan-grid--4 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 860px) {
  .plan-layout {
    grid-template-columns: 1fr;
  }

  .plan-summary {
    position: static;
  }
}

@media (max-width: 560px) {
  .plan-grid--4,
  .plan-grid--3 {
    grid-template-columns: 1fr;
  }

  .plan-span-2 {
    grid-column: auto;
  }

  .plan-chips {
    flex-direction: column;
    gap: 7px;
  }

  .plan-chips__label {
    width: auto;
    padding-top: 0;
  }
}
</style>
