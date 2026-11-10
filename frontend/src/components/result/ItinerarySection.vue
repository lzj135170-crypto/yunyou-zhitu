<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppButton from '../ui/AppButton.vue'
import EmptyState from '../ui/EmptyState.vue'
import Icon from '../ui/Icon.vue'
import SectionHeader from '../ui/SectionHeader.vue'
import WeatherIcon from '../ui/WeatherIcon.vue'
import { useImageFailures } from '../../composables/useImageFailures'
import { usePrefersReducedMotion } from '../../composables/usePrefersReducedMotion'
import { useSectionProgress } from '../../composables/useSectionProgress'
import { addDays, toISODate } from '../../utils/date'
import { formatTemp } from '../../utils/format'
import { splitBold } from '../../utils/inline'
import { scrollToId } from '../../utils/scroll'

const props = defineProps({
  plan: { type: Object, required: true },
  meta: { type: Object, default: () => ({}) },
  dayLabel: { type: Function, required: true },
  selectedDay: { type: [Number, String], default: null },
  routePlan: { type: Object, default: null },
  photoFor: { type: Function, default: () => '' },
})

const emit = defineEmits(['select-day'])
const openDays = ref(new Set())
const { hasFailed, markFailed } = useImageFailures()
const threadRef = ref(null)
const dayEls = ref([])
const litDays = ref(new Set())
const reduced = usePrefersReducedMotion()
const threadProgress = useSectionProgress(threadRef)
let dayObserver = null

const days = computed(() => (Array.isArray(props.plan.days) ? props.plan.days : []))

watch(
  days,
  (list) => {
    openDays.value = new Set(list.length ? [list[0].day] : [])
  },
  { immediate: true }
)

watch(
  () => props.selectedDay,
  (value) => {
    if (value === null || value === undefined) return
    const day = Number(value)
    if (!Number.isFinite(day)) return
    openDays.value = new Set([...openDays.value, day])
  },
  { immediate: true }
)

watch(
  days,
  () => {
    nextTick(observeDays)
  },
  { immediate: false }
)

function observeDays() {
  if (!dayObserver) return
  dayObserver.disconnect()
  litDays.value = new Set()
  dayEls.value.forEach((element) => {
    if (element) dayObserver.observe(element)
  })
}

function isLit(day) {
  return reduced.value || litDays.value.has(day.day)
}

onMounted(() => {
  if (typeof IntersectionObserver === 'undefined') {
    litDays.value = new Set(days.value.map((day) => day.day))
    return
  }
  dayObserver = new IntersectionObserver(
    (entries) => {
      const next = new Set(litDays.value)
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return
        const value = Number(entry.target.dataset.day)
        if (Number.isFinite(value)) next.add(value)
      })
      litDays.value = next
    },
    { rootMargin: '-12% 0px -52% 0px', threshold: 0 }
  )
  nextTick(observeDays)
})

onBeforeUnmount(() => {
  dayObserver?.disconnect()
  dayObserver = null
})

function isOpen(day) {
  return openDays.value.has(day.day)
}

function isSelected(day) {
  return Number(props.selectedDay) === day.day
}

function toggleDay(dayNumber) {
  const next = new Set(openDays.value)
  if (next.has(dayNumber)) next.delete(dayNumber)
  else next.add(dayNumber)
  openDays.value = next
}

function expandAll() {
  openDays.value = new Set(days.value.map((day) => day.day))
}

function collapseAll() {
  openDays.value = new Set()
}

function dayIso(day) {
  if (!props.meta || !props.meta.startDate) return ''
  const date = addDays(props.meta.startDate, day.day - 1)
  return date ? toISODate(date) : ''
}

function dayWeather(day) {
  const iso = dayIso(day)
  if (!iso) return null
  const list = props.plan.weather && Array.isArray(props.plan.weather.days)
    ? props.plan.weather.days
    : []
  return list.find((item) => item.date === iso) || null
}

function daySummary(day) {
  const text = [day.morning, day.afternoon, day.evening].filter(Boolean).join(' ')
  if (!text) return '暂无安排'
  const first = text.split(/[。！？]/)[0].trim()
  return first.length > 68 ? `${first.slice(0, 68)}…` : first
}

function dayPhoto(day) {
  const text = [day.morning, day.afternoon, day.evening].filter(Boolean).join(' ')
  if (!text) return null
  const attractions = Array.isArray(props.plan.attractions) ? props.plan.attractions : []
  const item = attractions.find((attraction) => attraction.name && text.includes(attraction.name))
  if (!item) return null
  const url = typeof props.photoFor === 'function' ? props.photoFor(item.name) : ''
  if (!url || hasFailed(url)) return null
  return { name: item.name, url }
}

function onPhotoError(url) {
  markFailed(url)
}

function focusOnMap(dayNumber) {
  emit('select-day', dayNumber)
  scrollToId('map')
}
</script>

<template>
  <section id="itinerary" class="result-section">
    <SectionHeader
      title="每日行程"
      description="按天拆解为上午 / 下午 / 晚上；有天气和景点照片时一起显示。"
    >
      <template v-if="days.length" #actions>
        <AppButton variant="ghost" size="sm" @click="expandAll">展开全部</AppButton>
        <AppButton variant="ghost" size="sm" @click="collapseAll">收起全部</AppButton>
      </template>
    </SectionHeader>

    <div
      v-if="days.length"
      ref="threadRef"
      class="itinerary-thread"
      :style="{ '--thread-progress': threadProgress }"
    >
      <article
        v-for="day in days"
        :key="day.day"
        ref="dayEls"
        :data-day="day.day"
        class="day"
        :class="{
          'is-open': isOpen(day),
          'is-selected': isSelected(day),
          'is-lit': isLit(day),
        }"
      >
        <div class="day__rail" aria-hidden="true">
          <span class="day__node"></span>
        </div>

        <div class="day__body">
          <header class="day__head">
            <button
              class="day__toggle"
              type="button"
              :aria-expanded="isOpen(day)"
              :aria-controls="`day-content-${day.day}`"
              @click="toggleDay(day.day)"
            >
              <span class="day__label mono">D{{ day.day }}</span>
              <span class="day__title">第 {{ day.day }} 天</span>
              <span v-if="dayLabel(day.day)" class="day__date num">{{ dayLabel(day.day) }}</span>
              <span v-if="dayWeather(day)" class="day__weather">
                <WeatherIcon :condition="dayWeather(day).weather" :size="15" />
                <span>{{ dayWeather(day).weather || '—' }}</span>
                <span class="num">
                  {{ formatTemp(dayWeather(day).temp_min) }}–{{ formatTemp(dayWeather(day).temp_max) }}
                </span>
              </span>
              <Icon
                :name="isOpen(day) ? 'chevron-down' : 'chevron-right'"
                :size="16"
                class="day__chevron"
              />
            </button>

            <button
              v-if="routePlan"
              class="day__map-link"
              type="button"
              @click="focusOnMap(day.day)"
            >
              在地图中查看
              <Icon name="map" :size="13" />
            </button>
          </header>

          <p v-if="!isOpen(day)" class="day__summary">
            <span
              v-for="(part, partIndex) in splitBold(daySummary(day))"
              :key="partIndex"
              :class="{ 'day__strong': part.strong }"
            >{{ part.text }}</span>
          </p>

          <div
            :id="`day-content-${day.day}`"
            class="day__content"
            :class="{ 'is-open': isOpen(day) }"
            :aria-hidden="!isOpen(day)"
          >
            <div
              class="day__content-inner"
              :class="{ 'has-photo': Boolean(dayPhoto(day)) }"
            >
              <div class="day__slots">
                <div v-if="day.morning" class="day__slot">
                  <span class="day__slot-label">上午</span>
                  <p>
                    <span
                      v-for="(part, partIndex) in splitBold(day.morning)"
                      :key="partIndex"
                      :class="{ 'day__strong': part.strong }"
                    >{{ part.text }}</span>
                  </p>
                </div>
                <div v-if="day.afternoon" class="day__slot">
                  <span class="day__slot-label">下午</span>
                  <p>
                    <span
                      v-for="(part, partIndex) in splitBold(day.afternoon)"
                      :key="partIndex"
                      :class="{ 'day__strong': part.strong }"
                    >{{ part.text }}</span>
                  </p>
                </div>
                <div v-if="day.evening" class="day__slot">
                  <span class="day__slot-label">晚上</span>
                  <p>
                    <span
                      v-for="(part, partIndex) in splitBold(day.evening)"
                      :key="partIndex"
                      :class="{ 'day__strong': part.strong }"
                    >{{ part.text }}</span>
                  </p>
                </div>
              </div>

              <figure v-if="dayPhoto(day)" class="day__photo">
                <img
                  :src="dayPhoto(day).url"
                  :alt="dayPhoto(day).name"
                  loading="lazy"
                  decoding="async"
                  referrerpolicy="no-referrer"
                  @error="onPhotoError(dayPhoto(day).url)"
                />
                <figcaption>{{ dayPhoto(day).name }} · 高德地图</figcaption>
              </figure>
            </div>
          </div>
        </div>
      </article>
    </div>

    <EmptyState
      v-else
      icon="calendar"
      title="这次没有生成按天行程"
      description="如果模型只回了文字，可以展开完整回复查看，或在底部继续调整。"
    />
  </section>
</template>

<style scoped>
.itinerary-thread {
  position: relative;
  margin-top: 22px;
}

.itinerary-thread::before {
  position: absolute;
  top: 8px;
  bottom: 10px;
  left: 7px;
  width: 1px;
  background: linear-gradient(var(--accent-strong), var(--accent-deep));
  content: "";
  transform: scaleY(var(--thread-progress, 0));
  transform-origin: top center;
  transition: transform 120ms linear;
}

.day {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr);
  gap: 18px;
  padding-bottom: 4px;
}

.day__rail {
  position: relative;
  display: flex;
  justify-content: center;
  padding-top: 8px;
}

.day__node {
  position: relative;
  z-index: 1;
  width: 7px;
  height: 7px;
  background: var(--accent);
  border-radius: 50%;
  box-shadow: 0 0 0 3px var(--paper);
  opacity: 0.38;
  transition:
    opacity var(--dur-2) var(--ease-out),
    box-shadow var(--dur-2) var(--ease-out),
    background-color var(--dur-2) var(--ease-out);
}

.day.is-lit .day__node {
  opacity: 1;
}

.day.is-selected .day__node {
  background: var(--accent);
  box-shadow:
    0 0 0 3px var(--paper),
    0 0 16px rgb(31 111 92 / 0.35);
}

.day__body {
  min-width: 0;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--rule);
}

.day__head {
  display: flex;
  gap: 10px;
  align-items: center;
  padding-bottom: 9px;
}

.day__toggle {
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;
  min-width: 0;
  padding: 0;
  color: inherit;
  background: transparent;
  border: 0;
  font: inherit;
  text-align: left;
}

.day__label {
  color: var(--accent-deep);
  font-size: var(--type-micro);
}

.day__title {
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: 1.02rem;
  font-weight: 650;
  line-height: 1.35;
}

.day__date {
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: var(--type-micro);
}

.day__weather {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  margin-left: auto;
  color: var(--text-muted);
  font-size: var(--type-micro);
}

.day__weather span {
  white-space: nowrap;
}

.day__chevron {
  color: var(--text-faint);
  transition: transform var(--dur-2) var(--ease-out);
}

.day__map-link {
  display: inline-flex;
  flex: none;
  gap: 4px;
  align-items: center;
  padding: 2px 0;
  color: var(--patina-deep);
  background: transparent;
  border: 0;
  font-size: var(--type-micro);
  white-space: nowrap;
}

.day__map-link:hover {
  color: var(--patina-deep);
  text-decoration: underline;
}

.day__summary {
  display: -webkit-box;
  max-width: 44em;
  margin-top: 7px;
  overflow: hidden;
  color: var(--text-muted);
  font-size: var(--type-small);
  line-height: 1.7;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.day__strong {
  color: var(--ink);
  font-weight: 650;
}

.day__content {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows var(--dur-3) var(--ease-out);
}

.day__content.is-open {
  grid-template-rows: 1fr;
}

.day__content-inner {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 18px;
  min-height: 0;
  overflow: hidden;
}

.day__content-inner.has-photo {
  grid-template-columns: minmax(0, 1fr) 168px;
}

.day__slots {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.day__slot {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--rule);
}

.day__slot:last-child {
  border-bottom: 0;
}

.day__slot-label {
  padding-top: 1px;
  color: var(--text-faint);
  font-family: var(--font-mono);
  font-size: var(--type-micro);
}

.day__slot p {
  max-width: 44em;
  color: var(--text);
  font-size: var(--type-small);
  line-height: 1.8;
  white-space: pre-wrap;
}

.day__photo {
  align-self: start;
  margin: 10px 0 0;
}

.day__photo img {
  display: block;
  width: 100%;
  height: 108px;
  object-fit: cover;
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
}

.day__photo figcaption {
  margin-top: 5px;
  color: var(--text-faint);
  font-size: 0.58rem;
  line-height: 1.5;
}

@media (prefers-reduced-motion: reduce) {
  .itinerary-thread::before {
    transform: scaleY(1);
    transition: none;
  }

  .day__node {
    opacity: 1;
  }
}

@media (max-width: 640px) {
  .day {
    gap: 12px;
  }

  .day__head {
    align-items: flex-start;
    flex-direction: column;
    gap: 5px;
  }

  .day__map-link {
    margin-left: 32px;
  }

  .day__weather {
    margin-left: 0;
  }

  .day__content-inner.has-photo {
    grid-template-columns: 1fr;
  }

  .day__photo {
    width: 100%;
  }

  .day__photo img {
    height: 150px;
  }

  .day__slot {
    grid-template-columns: 1fr;
    gap: 2px;
  }

  .day__slot-label {
    color: var(--text-muted);
  }
}
</style>
