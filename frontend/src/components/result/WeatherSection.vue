<script setup>
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'
import SectionHeader from '../ui/SectionHeader.vue'
import Sparkline from '../ui/Sparkline.vue'
import WeatherIcon from '../ui/WeatherIcon.vue'
import { formatMonthDay, weekday } from '../../utils/date'
import { formatTemp } from '../../utils/format'

const props = defineProps({
  weather: { type: Object, default: null },
})

const days = computed(() =>
  props.weather && Array.isArray(props.weather.days) ? props.weather.days : []
)

const tempTrend = computed(() =>
  days.value
    .map((day) => Number(day.temp_max))
    .filter((value) => Number.isFinite(value))
)

const bounds = computed(() => {
  const values = []
  days.value.forEach((day) => {
    const min = Number(day.temp_min)
    const max = Number(day.temp_max)
    if (Number.isFinite(min)) values.push(min)
    if (Number.isFinite(max)) values.push(max)
  })
  if (!values.length) return null
  const min = Math.min(...values)
  const max = Math.max(...values)
  return { min, max, span: Math.max(1, max - min) }
})

function hasTemp(day) {
  return Number.isFinite(Number(day.temp_min)) && Number.isFinite(Number(day.temp_max))
}

function rangeStyle(day) {
  if (!hasTemp(day) || !bounds.value) return {}
  const low = Math.min(Number(day.temp_min), Number(day.temp_max))
  const high = Math.max(Number(day.temp_min), Number(day.temp_max))
  const left = ((low - bounds.value.min) / bounds.value.span) * 100
  const width = ((high - low) / bounds.value.span) * 100
  return {
    left: `${Math.max(0, Math.min(100, left))}%`,
    width: `${Math.max(4, Math.min(100, width))}%`,
  }
}

function hasPrecip(day) {
  return Number.isFinite(Number(day.precip_prob))
}

function precipStyle(day) {
  if (!hasPrecip(day)) return { width: '0%' }
  return { width: `${Math.max(0, Math.min(100, Number(day.precip_prob)))}%` }
}

function precipLabel(day) {
  return hasPrecip(day) ? `${Math.round(Number(day.precip_prob))}%` : '—'
}
</script>

<template>
  <section id="weather" class="result-section weather-section">
    <SectionHeader
      title="天气信息"
      :description="`${weather && weather.city ? weather.city : '目的地'} · 真实预报，出发前请再确认一次。`"
    />

    <template v-if="days.length">
      <div class="weather-strip" role="list" aria-label="逐日天气预报">
        <article
          v-for="(day, index) in days"
          :key="day.date || index"
          class="weather-day"
          role="listitem"
        >
          <header class="weather-day__date">
            <strong>{{ formatMonthDay(day.date) }}</strong>
            <span>{{ weekday(day.date) }}</span>
          </header>

          <div class="weather-day__condition">
            <WeatherIcon :condition="day.weather" :size="20" />
            <span>{{ day.weather || '—' }}</span>
          </div>

          <p class="weather-day__temps num">
            <span class="weather-day__min">{{ formatTemp(day.temp_min) }}</span>
            <span class="weather-day__sep">/</span>
            <span class="weather-day__max">{{ formatTemp(day.temp_max) }}</span>
          </p>

          <div class="weather-day__range" aria-hidden="true">
            <span
              v-if="hasTemp(day)"
              class="weather-day__range-fill"
              :style="rangeStyle(day)"
            ></span>
          </div>

          <div class="weather-day__precip">
            <span>降水</span>
            <div class="weather-day__precip-track" aria-hidden="true">
              <span :style="precipStyle(day)"></span>
            </div>
            <span class="num">{{ precipLabel(day) }}</span>
          </div>
        </article>
      </div>

      <div v-if="tempTrend.length > 1" class="weather-trend">
        <div class="weather-trend__head">
          <span class="mono">最高温趋势</span>
          <span class="num">{{ formatTemp(tempTrend[0]) }} → {{ formatTemp(tempTrend[tempTrend.length - 1]) }}</span>
        </div>
        <Sparkline :points="tempTrend" :height="34" />
      </div>

      <details v-if="weather.summary || weather.advice" class="weather-details">
        <summary>
          <span>天气说明与建议</span>
          <Icon name="chevron-down" :size="14" class="weather-details__chevron" />
        </summary>
        <p v-if="weather.summary" class="weather-summary">{{ weather.summary }}</p>
        <p v-if="weather.advice" class="weather-advice">
          <Icon name="info" :size="15" />
          <span>{{ weather.advice }}</span>
        </p>
      </details>

      <p class="weather-source mono">
        <span>数据来源 · {{ weather.source || '未记录' }}</span>
        <span v-if="weather.city"> · {{ weather.city }}</span>
      </p>
    </template>

    <p v-else-if="weather && weather.error" class="note note--error">
      <Icon name="alert" :size="15" />
      <span>{{ weather.error }}</span>
    </p>
    <p v-else class="note">这次没有获取到天气数据。</p>
  </section>
</template>

<style scoped>
.weather-section {
  display: flex;
  flex-direction: column;
}

.weather-strip {
  display: flex;
  gap: 8px;
  margin-top: 16px;
  padding-bottom: 6px;
  overflow-x: auto;
  scroll-snap-type: x mandatory;
  scrollbar-width: none;
}

.weather-strip::-webkit-scrollbar {
  display: none;
}

.weather-day {
  display: flex;
  flex: 1 0 112px;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  padding: 9px;
  background: var(--paper-raised);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
  scroll-snap-align: start;
}

.weather-day__date {
  display: flex;
  flex-wrap: nowrap;
  gap: 4px;
  align-items: baseline;
  justify-content: space-between;
}

.weather-day__date strong {
  color: var(--ink);
  font-size: var(--type-small);
  font-weight: 600;
  white-space: nowrap;
}

.weather-day__date span {
  flex: none;
  color: var(--text-faint);
  font-family: var(--font-mono);
  font-size: 0.58rem;
  white-space: nowrap;
}

.weather-day__condition {
  display: flex;
  gap: 6px;
  align-items: center;
  color: var(--text);
  font-size: var(--type-label);
  line-height: 1.35;
}

.weather-day__condition span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.weather-day__temps {
  display: flex;
  gap: 3px;
  align-items: baseline;
  color: var(--ink);
  font-family: var(--font-mono);
  line-height: 1.2;
}

.weather-day__min {
  color: var(--text-muted);
  font-size: var(--type-label);
}

.weather-day__sep {
  color: var(--text-faint);
  font-size: 0.58rem;
}

.weather-day__max {
  font-size: var(--type-body);
}

.weather-day__range {
  position: relative;
  height: 3px;
  overflow: hidden;
  background: var(--rule);
  border-radius: 2px;
}

.weather-day__range-fill {
  position: absolute;
  top: 0;
  bottom: 0;
  background: linear-gradient(90deg, var(--accent-deep), var(--accent));
  border-radius: 2px;
  transform-origin: left center;
  animation: weather-bar 600ms var(--ease-out) both;
}

.weather-day__precip {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 5px;
  align-items: center;
  color: var(--text-faint);
  font-size: 0.58rem;
}

.weather-day__precip-track {
  height: 2px;
  overflow: hidden;
  background: var(--rule);
  border-radius: 1px;
}

.weather-day__precip-track span {
  display: block;
  height: 100%;
  background: var(--patina);
  border-radius: 1px;
  transform-origin: left center;
  animation: weather-bar 600ms var(--ease-out) both;
}

.weather-trend {
  margin-top: 12px;
  padding: 9px 0 4px;
  border-top: 1px solid var(--rule);
}

.weather-trend__head {
  display: flex;
  gap: 10px;
  align-items: baseline;
  justify-content: space-between;
  color: var(--text-faint);
  font-size: 0.58rem;
}

.weather-trend__head .num {
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: var(--type-micro);
}

.weather-trend :deep(.sparkline) {
  margin-top: 3px;
}

.weather-details {
  margin-top: auto;
  padding-top: 10px;
  border-top: 1px solid var(--rule);
}

.weather-details summary {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  color: var(--text-muted);
  font-size: var(--type-label);
  cursor: pointer;
  list-style: none;
}

.weather-details summary::-webkit-details-marker {
  display: none;
}

.weather-details summary:hover {
  color: var(--ink);
}

.weather-details__chevron {
  color: var(--text-faint);
  transition: transform var(--quick) var(--ease);
}

.weather-details[open] .weather-details__chevron {
  transform: rotate(180deg);
}

.weather-summary {
  max-width: 66ch;
  margin-top: 12px;
  color: var(--text);
  font-size: var(--type-small);
  line-height: 1.75;
}

.weather-advice {
  display: flex;
  gap: 7px;
  align-items: flex-start;
  margin-top: 10px;
  color: var(--text-muted);
  font-size: var(--type-small);
  line-height: 1.7;
}

.weather-advice svg {
  margin-top: 1px;
  color: var(--patina-deep);
}

.weather-source {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  margin-top: auto;
  padding-top: 12px;
  color: var(--text-faint);
  border-top: 1px solid var(--rule);
  font-size: 0.58rem;
  letter-spacing: 0.06em;
}

.weather-details + .weather-source {
  margin-top: 12px;
}

.note {
  display: flex;
  gap: 7px;
  align-items: flex-start;
  margin-top: 16px;
  padding: 10px 12px;
  color: var(--text-muted);
  background: var(--paper-deep);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
  font-size: var(--type-small);
}

.note--error {
  color: var(--vermilion);
  background: var(--vermilion-soft);
  border-color: rgb(192 73 61 / 0.3);
}

@keyframes weather-bar {
  from {
    transform: scaleX(0);
  }
  to {
    transform: scaleX(1);
  }
}

@media (prefers-reduced-motion: reduce) {
  .weather-day__range-fill,
  .weather-day__precip-track span {
    animation: none;
  }
}

@media (min-width: 1181px) and (max-width: 1240px) {
  .weather-day {
    flex-basis: 104px;
  }
}

@media (max-width: 760px) {
  .weather-day {
    flex-basis: 124px;
  }
}

@media (max-width: 430px) {
  .weather-day {
    flex-basis: 108px;
  }
}
</style>
