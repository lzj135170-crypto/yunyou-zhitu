<script setup>
import { computed } from 'vue'
import AppButton from '../ui/AppButton.vue'
import AppInput from '../ui/AppInput.vue'
import AppTabs from '../ui/AppTabs.vue'
import Icon from '../ui/Icon.vue'
import SectionHeader from '../ui/SectionHeader.vue'
import Tag from '../ui/Tag.vue'
import { formatCost, formatDistance, formatDuration } from '../../utils/format'
import { modeLabel } from '../../utils/labels'
import { routeModeColor } from '../../utils/routeModes'

const props = defineProps({
  routePlan: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  warnings: { type: Array, default: () => [] },
  selectedDay: { type: [Number, String], default: null },
  daysToShow: { type: Array, default: () => [] },
  hotelAddress: { type: String, default: '' },
  selectedHotel: { type: Object, default: null },
  stale: { type: Boolean, default: false },
  routableCount: { type: Number, default: 0 },
})

const emit = defineEmits(['generate', 'clear-hotel', 'update-address', 'select-day', 'scroll-map'])

const dayTabs = computed(() => {
  const days = (props.routePlan && props.routePlan.days) || []
  return [
    { id: 'all', label: '全部' },
    ...days.map((day) => ({ id: String(day.day), label: `第 ${day.day} 天` })),
  ]
})

const activeDayKey = computed(() =>
  props.selectedDay === null || props.selectedDay === undefined ? 'all' : String(props.selectedDay)
)

function onDayChange(value) {
  emit('select-day', value === 'all' ? null : Number(value))
}
</script>

<template>
  <section id="routes" class="result-section">
    <SectionHeader
      title="按天路线"
      description="按天串联景点，比较步行、骑行、公交和打车；打车费用为估算。"
    >
      <template #actions>
        <AppButton v-if="routePlan" variant="ghost" size="sm" @click="emit('scroll-map')">
          <Icon name="map" :size="15" />
          在地图中查看
        </AppButton>
        <AppButton
          variant="primary"
          size="sm"
          :loading="loading"
          :disabled="!routableCount"
          @click="emit('generate')"
        >
          {{ routePlan ? '重新计算' : '生成路线' }}
        </AppButton>
      </template>
    </SectionHeader>

    <div class="route-origin">
      <AppInput
        :model-value="hotelAddress"
        label="酒店地址 / 名称"
        placeholder="不填也可以，例如：成都春熙路希尔顿酒店"
        class="route-origin__field"
        @update:model-value="emit('update-address', $event)"
        @keyup.enter="emit('generate')"
      />
      <AppButton
        v-if="hotelAddress || routePlan"
        variant="ghost"
        size="sm"
        class="route-origin__clear"
        @click="emit('clear-hotel')"
      >
        清除
      </AppButton>
      <Tag v-if="selectedHotel" class="route-origin__tag">酒店：{{ selectedHotel.name }}</Tag>
    </div>
    <p class="route-hint">
      不填酒店也可以，会按景点顺序规划；地址只保存在本浏览器，坐标不落盘。
    </p>
    <p v-if="stale" class="route-stale">
      <Icon name="alert" :size="14" />
      地址已修改，请重新生成。
    </p>

    <p v-if="routePlan && routePlan.hotel" class="route-hotel">
      起终点：<strong>{{ routePlan.hotel.name }}</strong>
      <span v-if="routePlan.hotel.address"> · {{ routePlan.hotel.address }}</span>
    </p>

    <p v-if="loading && !routePlan" class="route-state">
      <span class="route-dot route-dot--pulse" aria-hidden="true"></span>
      正在按天计算路线，可能需要十几秒……
    </p>

    <div v-if="error" class="route-state route-state--error">
      <Icon name="alert" :size="15" />
      <span>{{ error }}</span>
      <AppButton variant="ghost" size="sm" :disabled="loading" @click="emit('generate')">
        重试
      </AppButton>
    </div>

    <div v-if="!routePlan && !loading && !error" class="route-empty">
      <p>还没有生成路线。按已加载的景点坐标，计算逐天交通方案。</p>
      <AppButton
        variant="primary"
        size="sm"
        :disabled="!routableCount"
        @click="emit('generate')"
      >
        生成路线
      </AppButton>
    </div>

    <template v-if="routePlan && routePlan.days && routePlan.days.length">
      <AppTabs
        :model-value="activeDayKey"
        :tabs="dayTabs"
        variant="instrument"
        aria-label="路线天数"
        class="route-day-tabs"
        @update:model-value="onDayChange"
      />

      <div class="route-days">
        <section v-for="day in daysToShow" :key="day.day" class="route-day">
          <header class="route-day__head">
            <h3>第 {{ day.day }} 天</h3>
            <div class="route-day__totals num">
              <span>{{ formatDistance(day.total_distance_m) }}</span>
              <span>{{ formatDuration(day.total_duration_s) }}</span>
              <span>
                {{ formatCost(day.total_cost_yuan) }}
                <em v-if="day.cost_is_estimate">含估算</em>
              </span>
            </div>
          </header>

          <div v-if="day.places && day.places.length" class="route-stops">
            <span v-for="(name, index) in day.places" :key="name" class="route-stop-group">
              <span class="route-stop"><i>{{ index + 1 }}</i>{{ name }}</span>
              <Icon
                v-if="index < day.places.length - 1"
                name="chevron-right"
                :size="13"
                class="route-stop__arrow"
              />
            </span>
          </div>
          <p v-else class="route-day__empty">这一天没有可规划的景点。</p>

          <div v-if="day.legs && day.legs.length" class="route-legs">
            <article v-for="(leg, index) in day.legs" :key="index" class="route-leg">
              <div class="route-leg__head">
                <span class="route-mode">
                  <i
                    aria-hidden="true"
                    :style="{ background: routeModeColor(leg.recommended.mode) }"
                  ></i>
                  {{ modeLabel(leg.recommended.mode, leg.recommended.mode_label) }}
                </span>
                <span class="route-leg__path">{{ leg.from_name }} → {{ leg.to_name }}</span>
                <Tag v-if="leg.recommended.estimated" tone="danger">直线估算</Tag>
                <span class="route-leg__metrics num">
                  {{ formatDistance(leg.recommended.distance_m) }}
                  · {{ formatDuration(leg.recommended.duration_s) }}
                  · {{ formatCost(leg.recommended.cost_yuan, leg.recommended.cost_is_estimate) }}
                </span>
              </div>
              <p v-if="leg.recommended.summary" class="route-leg__summary">
                {{ leg.recommended.summary }}
              </p>
              <details v-if="leg.alternatives && leg.alternatives.length" class="route-alt">
                <summary>备选方案（{{ leg.alternatives.length }}）</summary>
                <div v-for="alt in leg.alternatives" :key="alt.mode" class="route-alt__row">
                  <span>{{ modeLabel(alt.mode, alt.mode_label) }}</span>
                  <span class="num">
                    {{ formatDistance(alt.distance_m) }} · {{ formatDuration(alt.duration_s) }} ·
                    {{ formatCost(alt.cost_yuan, alt.cost_is_estimate) }}
                  </span>
                </div>
              </details>
            </article>
          </div>

          <ul v-if="day.warnings && day.warnings.length" class="route-warnings">
            <li v-for="(warning, index) in day.warnings" :key="index">
              <Icon name="alert" :size="13" />
              {{ warning }}
            </li>
          </ul>
        </section>
      </div>

      <ul v-if="warnings.length" class="route-warnings route-warnings--global">
        <li v-for="(warning, index) in warnings" :key="index">
          <Icon name="alert" :size="13" />
          {{ warning }}
        </li>
      </ul>

      <p class="route-disclaimer">
        推荐方案按耗时与费用综合计算；打车费用为估算，以实际计价为准。
      </p>
    </template>
  </section>
</template>

<style scoped>
.route-origin {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  margin-top: 18px;
}

.route-origin__field {
  flex: 1;
  min-width: 240px;
}

.route-origin__clear {
  margin-bottom: 1px;
}

.route-origin__tag {
  margin-bottom: 10px;
}

.route-hint {
  margin-top: 7px;
  color: var(--text-faint);
  font-size: var(--type-micro);
}

.route-stale {
  display: flex;
  gap: 5px;
  align-items: center;
  margin-top: 8px;
  color: var(--vermilion);
  font-size: var(--type-label);
}

.route-hotel {
  margin-top: 12px;
  padding: 8px 10px;
  color: var(--text);
  background: var(--paper-deep);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
  font-size: var(--type-small);
}

.route-state {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 16px;
  padding: 10px 12px;
  color: var(--text-muted);
  background: var(--paper-deep);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
  font-size: var(--type-small);
}

.route-empty {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  margin-top: 16px;
  padding: 14px;
  background: var(--paper-raised);
  border: 1px solid var(--rule-strong);
  border-radius: var(--radius-sm);
}

.route-empty p {
  flex: 1;
  min-width: 180px;
  color: var(--text-muted);
  font-size: var(--type-small);
  line-height: 1.65;
}

.route-state--error {
  color: var(--vermilion);
  background: var(--vermilion-soft);
  border-color: rgb(192 73 61 / 0.26);
}

.route-state--error :deep(.btn) {
  margin-left: auto;
}

.route-dot {
  width: 6px;
  height: 6px;
  background: var(--accent);
  border-radius: 50%;
}

.route-dot--pulse {
  animation: dot-pulse 1.2s ease-in-out infinite;
}

.route-day-tabs {
  margin-top: 16px;
}

.route-days {
  margin-top: 14px;
}

.route-day {
  padding: 14px 0 18px;
  border-bottom: 1px solid var(--rule);
}

.route-day__head {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  align-items: baseline;
  justify-content: space-between;
}

.route-day__head h3 {
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: 1rem;
  font-weight: 650;
}

.route-day__totals {
  display: flex;
  flex-basis: 100%;
  flex-wrap: wrap;
  gap: 6px 10px;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: var(--type-micro);
}

.route-day__totals em {
  color: var(--text-faint);
  font-style: normal;
}

.route-stops {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  align-items: center;
  margin-top: 9px;
}

.route-stop-group {
  display: inline-flex;
  gap: 5px;
  align-items: center;
}

.route-stop {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  padding: 3px 7px;
  color: var(--text);
  background: var(--paper-deep);
  border-radius: var(--radius-sm);
  font-size: var(--type-micro);
}

.route-stop i {
  color: var(--text-faint);
  font-family: var(--font-mono);
  font-style: normal;
}

.route-stop__arrow {
  color: var(--text-faint);
}

.route-day__empty {
  margin-top: 8px;
  color: var(--text-faint);
  font-size: var(--type-small);
}

.route-legs {
  margin-top: 10px;
  border-top: 1px solid var(--rule);
}

.route-leg {
  padding: 10px 0;
  border-bottom: 1px solid var(--rule);
}

.route-leg:last-child {
  border-bottom: 0;
}

.route-leg__head {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  align-items: center;
}

.route-mode {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  color: var(--ink);
  font-size: var(--type-label);
  font-weight: 600;
}

.route-mode i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.route-leg__path {
  min-width: 0;
  color: var(--text);
  font-size: var(--type-small);
  overflow-wrap: anywhere;
}

.route-leg__metrics {
  width: 100%;
  margin-left: 0;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: var(--type-micro);
  line-height: 1.5;
}

.route-leg__summary {
  margin-top: 4px;
  color: var(--text-muted);
  font-size: var(--type-label);
  line-height: 1.65;
}

.route-alt {
  margin-top: 5px;
}

.route-alt summary {
  color: var(--patina-deep);
  font-size: var(--type-micro);
  cursor: pointer;
}

.route-alt__row {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  padding: 4px 0 0 12px;
  color: var(--text-muted);
  font-size: var(--type-micro);
}

.route-warnings {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 10px;
}

.route-warnings--global {
  margin-top: 14px;
}

.route-warnings li {
  display: flex;
  gap: 5px;
  align-items: flex-start;
  color: var(--vermilion);
  font-size: var(--type-micro);
  line-height: 1.6;
}

.route-warnings svg {
  flex: none;
  margin-top: 1px;
}

.route-disclaimer {
  margin-top: 12px;
  color: var(--text-faint);
  font-size: var(--type-micro);
  line-height: 1.6;
}

@keyframes dot-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

@media (max-width: 640px) {
  .route-day__head {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }

  .route-leg__metrics {
    width: 100%;
    margin-left: 0;
  }
}
</style>
