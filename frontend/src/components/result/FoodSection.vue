<script setup>
import { nextTick, ref, watch } from 'vue'
import AppButton from '../ui/AppButton.vue'
import EmptyState from '../ui/EmptyState.vue'
import FoodPhoto from './FoodPhoto.vue'
import GalleryRail from '../ui/GalleryRail.vue'
import Icon from '../ui/Icon.vue'
import SectionHeader from '../ui/SectionHeader.vue'
import Spinner from '../ui/Spinner.vue'
import Tag from '../ui/Tag.vue'
import { amapMarkerUrl, formatAvgCost } from '../../utils/format'

const props = defineProps({
  plan: { type: Object, required: true },
  target: { type: Object, default: null },
  places: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  isTarget: { type: Function, required: true },
  buttonLabel: { type: Function, required: true },
  photoFailed: { type: Function, required: true },
  foodPhotoFor: { type: Function, required: true },
  foodPhotoLoading: { type: Function, required: true },
  foodPhotoFailed: { type: Function, required: true },
})

const emit = defineEmits(['toggle', 'close', 'photo-error', 'load-photo'])
const panel = ref(null)

watch(
  () => props.target,
  async (value) => {
    if (!value) return
    await nextTick()
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    panel.value?.scrollIntoView({
      behavior: reduce ? 'auto' : 'smooth',
      block: 'nearest',
    })
  }
)
</script>

<template>
  <section id="food" class="result-section">
    <SectionHeader
      title="美食推荐"
      description="当地必吃、小吃与美食街；人均为专家估算，营业情况和价格以商家实际为准。"
    />

    <GalleryRail
      v-if="plan.foods && plan.foods.length"
      label="美食推荐横向浏览"
      prev-label="上一批美食"
      next-label="下一批美食"
      class="food-rail"
    >
      <article v-for="item in plan.foods" :key="item.name" class="food-card">
        <FoodPhoto
          :item="item"
          :photo="foodPhotoFor(item)"
          :loading="foodPhotoLoading(item)"
          :failed="foodPhotoFailed(item)"
          @load="emit('load-photo', $event)"
        />

        <div class="food-card__body">
          <header class="food-card__head">
            <div class="food-card__title">
              <span class="food-card__mark" aria-hidden="true"></span>
              <h3>{{ item.name }}</h3>
            </div>
          </header>

          <p class="food-card__area mono">{{ item.area || '目的地' }}</p>

          <div class="food-card__tags">
            <Tag v-if="item.category">{{ item.category }}</Tag>
            <Tag v-if="item.meal_time">{{ item.meal_time }}</Tag>
          </div>

          <p v-if="item.price_range" class="food-card__meta">
            参考 {{ item.price_range }}
          </p>
          <p class="food-card__reason">{{ item.reason || '暂无推荐理由' }}</p>

          <button class="food-card__action food__action" type="button" @click="emit('toggle', item)">
            {{ buttonLabel(item) }}
            <Icon name="utensils" :size="13" />
          </button>
        </div>
      </article>
    </GalleryRail>
    <p v-else-if="plan.food_note" class="note">{{ plan.food_note }}</p>
    <EmptyState
      v-else
      icon="utensils"
      title="这次没有美食推荐数据"
      description="可以回到规划页补充饮食偏好，或在底部继续调整行程。"
    />

    <p v-if="plan.food_note && plan.foods && plan.foods.length" class="food__note">
      {{ plan.food_note }}
    </p>

    <section v-if="target" ref="panel" class="food-nearby">
      <header class="food-nearby__head">
        <div>
          <h3>{{ target.name }} 附近餐厅</h3>
          <p>高德实时检索；评分是高德 POI 评分，营业状态以商家为准，不含排队和订座。</p>
        </div>
        <AppButton variant="ghost" size="sm" @click="emit('close')">收起</AppButton>
      </header>

      <p v-if="loading" class="food-nearby__state">
        <Spinner :size="15" />
        正在查附近餐厅…
      </p>
      <p v-else-if="error" class="food-nearby__state food-nearby__state--error">
        <Icon name="alert" :size="15" />
        {{ error }}
      </p>

      <div v-else-if="places.length" class="restaurant-list">
        <article v-for="place in places" :key="place.name" class="restaurant">
          <div class="restaurant__thumb">
            <Icon name="utensils" :size="18" />
            <img
              v-if="place.photo_url && !photoFailed(place.photo_url)"
              class="restaurant__img"
              :src="place.photo_url"
              :alt="place.name"
              loading="lazy"
              decoding="async"
              referrerpolicy="no-referrer"
              @error="emit('photo-error', place.photo_url)"
            />
          </div>
          <div class="restaurant__body">
            <div class="restaurant__title">
              <strong>{{ place.name }}</strong>
              <span
                v-if="place.rating !== null && place.rating !== undefined"
                class="restaurant__rating"
                title="高德 POI 评分"
              >
                <Icon name="star" :size="12" filled />
                {{ place.rating }}
              </span>
            </div>
            <p class="restaurant__meta">
              <span v-if="place.category">{{ place.category }}</span>
              <span v-if="place.business_area">{{ place.business_area }}</span>
              <span v-if="place.avg_cost">参考 {{ formatAvgCost(place.avg_cost) }}</span>
            </p>
            <p v-if="place.address" class="restaurant__addr">{{ place.address }}</p>
            <a
              v-if="amapMarkerUrl(place)"
              class="restaurant__link"
              :href="amapMarkerUrl(place)"
              target="_blank"
              rel="noopener noreferrer"
            >
              在高德中查看
              <Icon name="arrow-up-right" :size="13" />
            </a>
          </div>
        </article>
      </div>

      <p v-else class="food-nearby__state">高德没有返回这一带可用的餐厅。</p>
    </section>
  </section>
</template>

<style scoped>
.food-rail {
  margin-top: 18px;
}

.food-card {
  display: flex;
  flex: 0 0 clamp(250px, 34%, 286px);
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
  background: var(--paper-raised);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
  scroll-snap-align: start;
  transition:
    border-color var(--dur-2) var(--ease-out),
    transform var(--dur-2) var(--ease-out);
}

.food-card__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  padding: 14px;
}

.food-card__head {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.food-card__title {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
}

.food-card__mark {
  flex: none;
  width: 6px;
  height: 6px;
  background: var(--accent);
  border-radius: 50%;
}

.food-card__title h3 {
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: 1rem;
  font-weight: 650;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.food-card__area {
  margin-top: 4px;
  color: var(--text-faint);
  font-size: 0.58rem;
}

.food-card__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 7px;
}

.food-card__meta {
  margin-top: 7px;
  color: var(--accent);
  font-size: var(--type-micro);
}

.food-card__reason {
  display: -webkit-box;
  margin-top: 5px;
  overflow: hidden;
  color: var(--text);
  font-size: var(--type-small);
  line-height: 1.7;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.food-card__action {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  align-self: flex-start;
  margin-top: auto;
  padding: 10px 0 0;
  color: var(--patina-deep);
  background: transparent;
  border: 0;
  font-size: var(--type-label);
}

.food-card__action:hover {
  color: var(--patina-deep);
  text-decoration: underline;
}

.food__note {
  margin-top: 12px;
  color: var(--text-muted);
  font-size: var(--type-small);
}

.food-nearby {
  margin-top: 20px;
  padding: 16px;
  background: var(--paper-raised);
  border: 1px solid var(--rule-strong);
  border-radius: var(--radius-md);
  scroll-margin-top: 96px;
}

.food-nearby__head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--rule);
}

.food-nearby__head h3 {
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: 1rem;
  font-weight: 650;
}

.food-nearby__head p {
  margin-top: 3px;
  color: var(--text-muted);
  font-size: var(--type-micro);
  line-height: 1.6;
}

.food-nearby__state {
  display: flex;
  gap: 7px;
  align-items: center;
  padding: 12px 0 2px;
  color: var(--text-muted);
  font-size: var(--type-small);
}

.food-nearby__state--error {
  color: var(--vermilion);
}

.restaurant-list {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 0;
}

.restaurant {
  display: grid;
  grid-template-columns: 60px minmax(0, 1fr);
  gap: 11px;
  padding: 12px 0;
  border-bottom: 1px solid var(--rule);
}

.restaurant__thumb {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 60px;
  height: 60px;
  overflow: hidden;
  color: var(--text-faint);
  background: var(--paper-deep);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
}

.restaurant__img {
  position: absolute;
  inset: 0;
  z-index: 1;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.restaurant__title {
  display: flex;
  gap: 8px;
  align-items: baseline;
  justify-content: space-between;
}

.restaurant__title strong {
  color: var(--ink);
  font-size: var(--type-small);
  font-weight: 650;
}

.restaurant__rating {
  display: inline-flex;
  flex: none;
  gap: 3px;
  align-items: center;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: var(--type-micro);
}

.restaurant__rating svg {
  color: var(--accent-strong);
}

.restaurant__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 2px;
  color: var(--text-muted);
  font-size: var(--type-micro);
}

.restaurant__addr {
  margin-top: 2px;
  color: var(--text-faint);
  font-size: var(--type-micro);
  line-height: 1.5;
}

.restaurant__link {
  display: inline-flex;
  gap: 3px;
  align-items: center;
  margin-top: 3px;
  color: var(--patina-deep);
  font-size: var(--type-micro);
  text-decoration: none;
}

.restaurant__link:hover {
  text-decoration: underline;
}

.note {
  margin-top: 16px;
  padding: 10px 12px;
  color: var(--text-muted);
  background: var(--paper-deep);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
  font-size: var(--type-small);
}

@media (hover: hover) {
  .food-card:hover {
    border-color: var(--rule-strong);
    transform: translateY(-2px);
  }
}

@media (max-width: 760px) {
  .food-card {
    flex-basis: min(82vw, 300px);
  }
}

@media (max-width: 640px) {
  .food-nearby__head {
    flex-direction: column;
    gap: 8px;
  }
}
</style>
