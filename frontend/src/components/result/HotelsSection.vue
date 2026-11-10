<script setup>
import { nextTick } from 'vue'
import AppTabs from '../ui/AppTabs.vue'
import EmptyState from '../ui/EmptyState.vue'
import GalleryRail from '../ui/GalleryRail.vue'
import Icon from '../ui/Icon.vue'
import PhotoPlate from '../ui/PhotoPlate.vue'
import SectionHeader from '../ui/SectionHeader.vue'
import Spinner from '../ui/Spinner.vue'
import Tag from '../ui/Tag.vue'
import { formatAvgCost } from '../../utils/format'

const props = defineProps({
  plan: { type: Object, required: true },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  tiersFor: { type: Function, required: true },
  tierKey: { type: Function, required: true },
  placesFor: { type: Function, required: true },
  tierError: { type: Function, required: true },
  selectTier: { type: Function, required: true },
  photoFailed: { type: Function, required: true },
  onPhotoError: { type: Function, required: true },
})

const emit = defineEmits(['select-hotel'])

const railRefs = []

function setRailRef(element, index) {
  railRefs[index] = element
}

async function onSelectTier(index, key) {
  props.selectTier(index, key)
  await nextTick()
  railRefs[index]?.scrollToStart()
}

function tierLabel(index) {
  const tier = props.tiersFor(index).find((item) => item.key === props.tierKey(index))
  return tier ? `${tier.label}档 · ${tier.places.length} 家` : ''
}
</script>

<template>
  <section id="hotels" class="result-section">
    <SectionHeader
      title="住宿建议"
      description="每个区域分舒适型与经济型/民宿两档检索；房价与房态以预订平台为准。"
    />

    <p v-if="loading" class="hotel-state">
      <Spinner :size="15" />
      正在检索这一带的酒店…
    </p>

    <div v-else-if="plan.hotels && plan.hotels.length" class="hotel-list">
      <section v-for="(item, index) in plan.hotels" :key="index" class="hotel-area">
        <header class="hotel-area__head">
          <div class="hotel-area__title">
            <span class="hotel-area__mark" aria-hidden="true"></span>
            <h3>{{ item.area }}</h3>
          </div>
          <p class="hotel-area__reason">{{ item.reason }}</p>
          <div class="hotel-area__tags">
            <Tag v-if="item.level">{{ item.level }}</Tag>
            <Tag v-if="item.suitable_for">{{ item.suitable_for }}</Tag>
            <Tag v-if="item.price_range" tone="accent">{{ item.price_range }}</Tag>
          </div>
        </header>

        <div v-if="!loading && !error" class="hotel-area__body">
          <AppTabs
            v-if="tiersFor(index).length"
            :model-value="tierKey(index)"
            :tabs="tiersFor(index).map((tier) => ({ id: tier.key, label: `${tier.label} ${tier.places.length}` }))"
            variant="instrument"
            :aria-label="`${item.area} 住宿档位`"
            @update:model-value="onSelectTier(index, $event)"
          />
          <p v-if="tierLabel(index)" class="hotel-area__tier-print">{{ tierLabel(index) }}</p>

          <GalleryRail
            v-if="placesFor(index).length"
            :ref="(element) => setRailRef(element, index)"
            class="hotel-rail"
            :label="`${item.area}酒店横向浏览`"
            prev-label="上一批酒店"
            next-label="下一批酒店"
          >
            <article v-for="place in placesFor(index)" :key="place.name" class="hotel-poi">
              <div class="hotel-poi__thumb">
                <PhotoPlate
                  v-if="place.photo_url && !photoFailed(place.photo_url)"
                  class="hotel-poi__photo"
                  :src="place.photo_url"
                  :alt="place.name"
                  :label="place.name"
                  @error="onPhotoError(place.photo_url)"
                />
                <Icon v-else name="hotel" :size="20" class="hotel-poi__thumb-icon" />
              </div>

              <div class="hotel-poi__body">
                <div class="hotel-poi__title">
                  <strong>{{ place.name }}</strong>
                  <span
                    v-if="place.rating !== null && place.rating !== undefined"
                    class="hotel-poi__rating"
                    title="高德 POI 评分"
                  >
                    <Icon name="star" :size="12" filled />
                    {{ place.rating }}
                  </span>
                </div>
                <p class="hotel-poi__meta">
                  <span v-if="place.category">{{ place.category }}</span>
                  <span v-if="place.business_area">{{ place.business_area }}</span>
                  <span v-if="place.avg_cost">参考 {{ formatAvgCost(place.avg_cost) }}</span>
                </p>
                <p v-if="place.address" class="hotel-poi__addr">{{ place.address }}</p>
                <button class="hotel-poi__plan" type="button" @click="emit('select-hotel', place)">
                  用这家规划路线
                </button>
              </div>
            </article>
          </GalleryRail>

          <p v-else-if="tierError(index)" class="hotel-state hotel-state--error">
            <Icon name="alert" :size="15" />
            {{ tierError(index) }}
          </p>
          <p v-else class="hotel-state">这一档暂时没有检索到酒店，可以切换另一种。</p>
        </div>
      </section>
    </div>

    <EmptyState
      v-else
      icon="hotel"
      title="这次没有住宿建议"
      description="可以回到规划页调整住宿偏好，或在底部继续调整行程。"
    />

    <p v-if="error" class="hotel-state hotel-state--error">
      <Icon name="alert" :size="15" />
      {{ error }}
    </p>
    <p v-if="plan.hotels && plan.hotels.length" class="hotel-disclaimer">
      酒店与图片来自高德地图检索，评分是高德 POI 评分；房价与房态以预订平台为准。
    </p>
  </section>
</template>

<style scoped>
.hotel-list {
  margin-top: 18px;
  border-top: 1px solid var(--rule);
}

.hotel-area {
  padding: 20px 0 24px;
  border-bottom: 1px solid var(--rule);
}

.hotel-area:last-child {
  border-bottom: 0;
}

.hotel-area__head {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.hotel-area__title {
  display: flex;
  gap: 9px;
  align-items: center;
}

.hotel-area__mark {
  width: 6px;
  height: 6px;
  background: var(--accent);
  border-radius: 50%;
}

.hotel-area__title h3 {
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: 1.05rem;
  font-weight: 650;
}

.hotel-area__reason {
  max-width: 72ch;
  color: var(--text);
  font-size: var(--type-small);
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.hotel-area__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.hotel-area__body {
  margin-top: 12px;
}

.hotel-area__tier-print {
  display: none;
}

.hotel-rail {
  margin-top: 10px;
}

.hotel-poi {
  display: flex;
  flex: 0 0 clamp(250px, 34%, 292px);
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
  background: var(--paper-raised);
  border: 1px solid var(--rule);
  border-radius: var(--radius-md);
  scroll-snap-align: start;
  transition:
    border-color var(--dur-2) var(--ease-out),
    transform var(--dur-2) var(--ease-out);
}

.hotel-poi__thumb {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 132px;
  overflow: hidden;
  color: var(--text-faint);
  background: var(--paper-deep);
  border-bottom: 1px solid var(--rule);
}

.hotel-poi__photo {
  width: 100%;
  height: 100%;
  min-height: 0;
  border: 0;
  border-radius: 0;
}

.hotel-poi__thumb-icon {
  color: var(--text-faint);
}

.hotel-poi__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  padding: 12px 14px 14px;
}

.hotel-poi__title {
  display: flex;
  gap: 8px;
  align-items: baseline;
  justify-content: space-between;
}

.hotel-poi__title strong {
  min-width: 0;
  color: var(--ink);
  font-size: var(--type-small);
  font-weight: 650;
  overflow-wrap: anywhere;
}

.hotel-poi__rating {
  display: inline-flex;
  flex: none;
  gap: 3px;
  align-items: center;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: var(--type-micro);
}

.hotel-poi__rating svg {
  color: var(--accent-strong);
}

.hotel-poi__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 2px;
  color: var(--text-muted);
  font-size: var(--type-micro);
}

.hotel-poi__addr {
  margin-top: 2px;
  color: var(--text-faint);
  font-size: var(--type-micro);
  line-height: 1.5;
}

.hotel-poi__plan {
  align-self: flex-start;
  margin-top: auto;
  padding: 10px 0 0;
  color: var(--patina-deep);
  background: transparent;
  border: 0;
  font-size: var(--type-micro);
  text-decoration: underline;
  text-decoration-color: var(--patina-pale);
  text-underline-offset: 0.18em;
}

.hotel-poi__plan:hover {
  text-decoration-color: var(--patina-deep);
}

.hotel-state {
  display: flex;
  gap: 7px;
  align-items: center;
  margin-top: 10px;
  padding: 9px 11px;
  color: var(--text-muted);
  background: var(--paper-deep);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
  font-size: var(--type-small);
}

.hotel-state--error {
  color: var(--vermilion);
  background: var(--vermilion-soft);
  border-color: rgb(192 73 61 / 0.26);
}

.hotel-disclaimer {
  margin-top: 10px;
  color: var(--text-faint);
  font-size: var(--type-micro);
  line-height: 1.6;
}

@media (hover: hover) {
  .hotel-poi:hover {
    border-color: var(--rule-strong);
    transform: translateY(-2px);
  }
}

@media (max-width: 760px) {
  .hotel-poi {
    flex-basis: min(82vw, 300px);
  }
}
</style>
