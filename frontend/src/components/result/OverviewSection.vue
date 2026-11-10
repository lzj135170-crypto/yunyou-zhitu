<script setup>
import { computed } from 'vue'
import AppButton from '../ui/AppButton.vue'
import PhotoPlate from '../ui/PhotoPlate.vue'
import StatBar from '../ui/StatBar.vue'
import { useImageFailures } from '../../composables/useImageFailures'
import { formatTemp } from '../../utils/format'
import { splitBold } from '../../utils/inline'
import { scrollToId } from '../../utils/scroll'

const props = defineProps({
  plan: { type: Object, required: true },
  meta: { type: Object, default: () => ({}) },
  tripDateRange: { type: String, default: '' },
  tripDayCount: { type: Number, default: 0 },
  photoMap: { type: Object, default: () => ({}) },
  mapUrl: { type: String, default: '' },
})

const destination = computed(
  () => props.plan.destination || props.meta.destination || '你的旅行'
)
const destinationInitial = computed(() => String(destination.value).trim().slice(0, 1) || '旅')
const overviewPlain = computed(() => String(props.plan.overview || '').replace(/\*\*/g, ''))

const photoEntries = computed(() =>
  Object.entries(props.photoMap || {}).filter(
    ([, url]) => typeof url === 'string' && url.length > 0
  )
)

const heroPhotoItem = computed(() => {
  const attractions = Array.isArray(props.plan.attractions) ? props.plan.attractions : []
  for (const item of attractions) {
    const url = props.photoMap && props.photoMap[item.name]
    if (url) return { name: item.name, url }
  }
  const first = photoEntries.value[0]
  return first ? { name: first[0], url: first[1] } : null
})

const { hasFailed, markFailed } = useImageFailures()

const heroPhoto = computed(() => {
  const item = heroPhotoItem.value
  if (!item || hasFailed(item.url)) return ''
  return item.url
})
const heroPhotoName = computed(() => heroPhotoItem.value?.name || '')

function onHeroPhotoError() {
  const item = heroPhotoItem.value
  if (item) markFailed(item.url)
}

const sourceCount = computed(() =>
  Array.isArray(props.plan.sources) ? props.plan.sources.length : 0
)

const budgetNumber = computed(() => {
  const value = Number(props.meta.budget)
  return Number.isFinite(value) && value > 0 ? value : 0
})

const budgetDisplay = computed(() =>
  budgetNumber.value ? `¥${budgetNumber.value.toLocaleString('zh-CN')}` : '未设置'
)

const perPersonBudget = computed(() => {
  const travelers = Number(props.meta.travelers)
  if (!budgetNumber.value || !Number.isFinite(travelers) || travelers <= 0) return '—'
  return `¥${Math.round(budgetNumber.value / travelers).toLocaleString('zh-CN')}`
})

const weatherDays = computed(() =>
  props.plan.weather && Array.isArray(props.plan.weather.days) ? props.plan.weather.days : []
)

const weatherRange = computed(() => {
  const values = []
  weatherDays.value.forEach((day) => {
    const min = Number(day.temp_min)
    const max = Number(day.temp_max)
    if (Number.isFinite(min)) values.push(min)
    if (Number.isFinite(max)) values.push(max)
  })
  if (!values.length) return '—'
  return `${formatTemp(Math.min(...values))}–${formatTemp(Math.max(...values))}`
})

const stats = computed(() => {
  const travelers = Number(props.meta.travelers)
  return [
    {
      label: '天数',
      value: String(props.tripDayCount || '—'),
      suffix: props.tripDayCount ? '天' : '',
      count: props.tripDayCount > 0 ? props.tripDayCount : null,
    },
    {
      label: '人数',
      value: travelers > 0 ? String(travelers) : '—',
      suffix: travelers > 0 ? '人' : '',
      count: travelers > 0 ? travelers : null,
    },
    {
      label: '目标预算',
      value: budgetDisplay.value,
      count: budgetNumber.value > 0 ? budgetNumber.value : null,
      countPrefix: '¥',
    },
    { label: '人均', value: perPersonBudget.value },
    { label: '天气区间', value: weatherRange.value },
    {
      label: '参考资料',
      value: sourceCount.value ? String(sourceCount.value) : '—',
      suffix: sourceCount.value ? '条' : '',
      count: sourceCount.value > 0 ? sourceCount.value : null,
    },
  ]
})
</script>

<template>
  <section id="overview" class="result-section overview-section">
    <div class="hero">
      <div class="hero__copy">
        <p class="hero__eyebrow mono">TRAVEL DASHBOARD / {{ tripDayCount }} DAYS</p>
        <h1 class="hero__title">{{ destination }}</h1>
        <p class="hero__dates num">
          {{ tripDateRange }}
          <span v-if="plan.weather && plan.weather.city"> · {{ plan.weather.city }}</span>
        </p>
        <p
          v-if="plan.overview"
          class="hero__overview"
          :title="overviewPlain"
        >
          <span
            v-for="(part, partIndex) in splitBold(plan.overview)"
            :key="partIndex"
            :class="{ 'hero__strong': part.strong }"
          >{{ part.text }}</span>
        </p>
        <p v-else class="hero__overview hero__overview--muted">
          这次回复没有单独的总览段落，可以直接看每日行程。
        </p>

        <div class="hero__actions">
          <AppButton variant="primary" arrow @click="scrollToId('map')">查看地图</AppButton>
          <AppButton variant="ghost" @click="scrollToId('adjust')">继续调整</AppButton>
        </div>
      </div>

      <figure class="hero__media">
        <PhotoPlate
          v-if="heroPhoto"
          :src="heroPhoto"
          :alt="heroPhotoName"
          :label="destinationInitial"
          eager
          @error="onHeroPhotoError"
        />
        <div v-else-if="mapUrl" class="hero__map">
          <img :src="mapUrl" alt="行程地图预览" loading="eager" decoding="async" />
          <span class="hero__media-tag mono">静态地图 · 高德</span>
        </div>
        <div v-else class="hero__placeholder">
          <span class="hero__placeholder-initial">{{ destinationInitial }}</span>
          <span class="hero__placeholder-rule"></span>
          <span class="hero__placeholder-hint mono">地图待生成</span>
        </div>

        <figcaption v-if="heroPhoto" class="hero__credit mono">
          图 / {{ heroPhotoName }} · 高德地图
        </figcaption>
        <figcaption v-else-if="mapUrl" class="hero__credit mono">
          地图 / 高德地图
        </figcaption>
      </figure>
    </div>

    <StatBar :items="stats" class="hero__stats" />
  </section>
</template>

<style scoped>
.overview-section {
  padding-top: 24px;
}

.hero {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(320px, 0.95fr);
  gap: clamp(24px, 4vw, 48px);
  align-items: stretch;
}

.hero__copy {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

.hero__eyebrow {
  color: var(--accent-deep);
  font-size: 0.6rem;
  letter-spacing: 0.16em;
}

.hero__title {
  color: var(--ink);
  font-family: var(--font-display);
  font-size: clamp(2.6rem, 5vw, 4.2rem);
  font-weight: 300;
  line-height: 1.02;
  overflow-wrap: anywhere;
}

.hero__dates {
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: var(--type-label);
}

.hero__overview {
  display: -webkit-box;
  max-width: 58ch;
  margin-top: 4px;
  overflow: hidden;
  color: var(--text);
  font-size: var(--type-body);
  line-height: 1.85;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.hero__overview--muted {
  color: var(--text-muted);
}

.hero__strong {
  color: var(--ink);
  font-weight: 650;
}

.hero__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: auto;
  padding-top: 18px;
}

.hero__media {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 320px;
  margin: 0;
}

.hero__media :deep(.photo-plate) {
  flex: 1;
}

.hero__media :deep(.photo-plate__img) {
  animation: hero-settle 900ms var(--ease-out) both;
}

.hero__map img {
  animation: hero-fade 600ms var(--ease-out) both;
}

@keyframes hero-settle {
  from {
    opacity: 0.35;
    transform: scale(1.03);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

@keyframes hero-fade {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .hero__media :deep(.photo-plate__img),
  .hero__map img {
    animation: none;
  }
}

.hero__map {
  position: relative;
  flex: 1;
  min-height: 240px;
  overflow: hidden;
  background: var(--paper-deep);
  border: 1px solid var(--rule-strong);
  border-radius: var(--radius-md);
  box-shadow: var(--glow-accent);
}

.hero__map img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.hero__media-tag {
  position: absolute;
  bottom: 10px;
  left: 10px;
  padding: 4px 7px;
  color: var(--text-muted);
  background: rgb(255 255 255 / 0.9);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
  font-size: 0.58rem;
}

.hero__placeholder {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 10px;
  align-items: center;
  justify-content: center;
  min-height: 240px;
  background:
    radial-gradient(120% 90% at 20% 0%, rgb(220 239 243 / 0.9), transparent 62%),
    linear-gradient(145deg, var(--paper-deep), var(--paper-raised));
  border: 1px solid var(--rule-strong);
  border-radius: var(--radius-md);
}

.hero__placeholder-initial {
  color: var(--accent);
  font-family: var(--font-display);
  font-size: clamp(3rem, 7vw, 5rem);
  font-weight: 300;
  line-height: 1;
}

.hero__placeholder-rule {
  width: 46px;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
}

.hero__placeholder-hint {
  color: var(--text-faint);
  font-size: 0.6rem;
  letter-spacing: 0.12em;
}

.hero__credit {
  margin-top: 7px;
  color: var(--text-faint);
  font-size: 0.58rem;
  letter-spacing: 0.06em;
  text-align: right;
}

.hero__stats {
  margin-top: 22px;
}

@media (max-width: 980px) {
  .hero {
    grid-template-columns: minmax(0, 1fr) minmax(260px, 0.85fr);
  }
}

@media (max-width: 760px) {
  .hero {
    grid-template-columns: 1fr;
  }

  .hero__title {
    font-size: clamp(2.4rem, 11vw, 3.4rem);
  }

  .hero__media {
    min-height: 220px;
  }

  .hero__overview {
    -webkit-line-clamp: 4;
  }
}
</style>
