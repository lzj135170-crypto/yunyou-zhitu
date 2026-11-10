<script setup>
import { computed } from 'vue'
import EmptyState from '../ui/EmptyState.vue'
import GalleryRail from '../ui/GalleryRail.vue'
import Icon from '../ui/Icon.vue'
import PhotoPlate from '../ui/PhotoPlate.vue'
import SectionHeader from '../ui/SectionHeader.vue'
import Tag from '../ui/Tag.vue'
import { formatHours } from '../../utils/format'

const props = defineProps({
  plan: { type: Object, required: true },
  photoFor: { type: Function, required: true },
  dropPhoto: { type: Function, required: true },
  hasPhotos: { type: Boolean, default: false },
  mapPoints: { type: Array, default: () => [] },
})

const attractions = computed(() =>
  Array.isArray(props.plan.attractions) ? props.plan.attractions : []
)

const featureIndex = computed(() =>
  attractions.value.findIndex((item) => Boolean(props.photoFor(item.name)))
)

function mapPointFor(name) {
  return props.mapPoints.find((point) => point.name === name) || null
}

function coordFor(name) {
  const point = mapPointFor(name)
  if (!point) return ''
  const longitude = Number(point.longitude)
  const latitude = Number(point.latitude)
  if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return ''
  return `${latitude.toFixed(4)}, ${longitude.toFixed(4)}`
}
</script>

<template>
  <section id="attractions" class="result-section">
    <SectionHeader
      title="景点推荐"
      description="来自景点专家与知识库；图片来自高德，没有图片时用首字占位。"
    />

    <GalleryRail
      v-if="attractions.length"
      label="景点推荐与照片"
      prev-label="上一批景点"
      next-label="下一批景点"
      class="attraction-rail"
    >
      <article
        v-for="(item, index) in attractions"
        :key="item.name"
        class="attraction-card"
        :class="{ 'attraction-card--feature': index === featureIndex }"
      >
        <PhotoPlate
          class="attraction-card__media"
          :src="photoFor(item.name)"
          :alt="item.name"
          :label="item.name"
          @error="dropPhoto(item.name)"
        />

        <div class="attraction-card__body">
          <div class="attraction-card__head">
            <h3>{{ item.name }}</h3>
            <Tag v-if="item.price_range" tone="accent">{{ item.price_range }}</Tag>
          </div>
          <p class="attraction-card__reason">{{ item.reason || '暂无推荐理由' }}</p>
          <div class="attraction-card__foot">
            <span v-if="item.hours" class="attraction-card__hours">
              <Icon name="clock" :size="13" />
              建议 {{ formatHours(item.hours) }}
            </span>
            <span v-if="coordFor(item.name)" class="attraction-card__coord mono">
              {{ coordFor(item.name) }}
            </span>
          </div>
        </div>
      </article>
    </GalleryRail>

    <EmptyState
      v-else
      icon="map-pin"
      title="这次没有景点推荐数据"
      description="可以回到规划页补充偏好，或在底部继续调整行程。"
    />

    <p v-if="hasPhotos" class="attraction__credit">景点图片来自高德地图</p>
  </section>
</template>

<style scoped>
.attraction-rail {
  margin-top: 18px;
}

.attraction-card {
  flex: 0 0 clamp(240px, 30%, 286px);
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

.attraction-card--feature {
  flex-basis: clamp(320px, 44%, 430px);
}

.attraction-card__media {
  height: 190px;
  min-height: 0;
  border: 0;
  border-bottom: 1px solid var(--rule);
  border-radius: 0;
}

.attraction-card--feature .attraction-card__media {
  height: 240px;
}

.attraction-card :deep(.photo-plate__img) {
  transition: transform var(--dur-2) var(--ease-out);
}

.attraction-card__body {
  padding: 13px 14px 14px;
}

.attraction-card__head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.attraction-card__head h3 {
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: 1.02rem;
  font-weight: 650;
  line-height: 1.35;
}

.attraction-card__reason {
  display: -webkit-box;
  margin-top: 6px;
  overflow: hidden;
  color: var(--text);
  font-size: var(--type-small);
  line-height: 1.7;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.attraction-card--feature .attraction-card__reason {
  -webkit-line-clamp: 4;
}

.attraction-card__foot {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 12px;
  align-items: center;
  margin-top: 10px;
  padding-top: 9px;
  border-top: 1px solid var(--rule);
}

.attraction-card__hours {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  color: var(--text-muted);
  font-size: var(--type-micro);
}

.attraction-card__coord {
  margin-left: auto;
  color: var(--text-faint);
  font-size: 0.58rem;
  letter-spacing: 0.03em;
}

.attraction__credit {
  margin-top: 8px;
  color: var(--text-faint);
  font-size: var(--type-micro);
}

@media (hover: hover) {
  .attraction-card:hover {
    border-color: var(--rule-strong);
    transform: translateY(-2px);
  }

  .attraction-card:hover :deep(.photo-plate__img) {
    transform: scale(1.03);
  }
}

@media (max-width: 760px) {
  .attraction-card {
    flex-basis: min(80vw, 320px);
  }

  .attraction-card--feature {
    flex-basis: min(86vw, 340px);
  }

  .attraction-card__media,
  .attraction-card--feature .attraction-card__media {
    height: 180px;
  }
}
</style>
