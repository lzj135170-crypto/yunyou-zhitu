<script setup>
import { computed } from 'vue'
import AppTabs from '../ui/AppTabs.vue'
import SectionHeader from '../ui/SectionHeader.vue'
import TripMap from '../map/TripMap.vue'
import { mapLabel } from '../../utils/format'

const props = defineProps({
  mapUrl: { type: String, default: '' },
  points: { type: Array, default: () => [] },
  hotels: { type: Array, default: () => [] },
  routes: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  legendAttractions: { type: Array, default: () => [] },
  legendItems: { type: Array, default: () => [] },
  hasEstimated: { type: Boolean, default: false },
  selectedDay: { type: [Number, String], default: null },
  routeDays: { type: Array, default: () => [] },
})

const emit = defineEmits(['select-day'])

const mapDayTabs = computed(() => [
  { id: 'all', label: '全部' },
  ...props.routeDays.map((day) => ({ id: String(day.day), label: `D${day.day}` })),
])

const activeDayKey = computed(() =>
  props.selectedDay === null || props.selectedDay === undefined
    ? 'all'
    : String(props.selectedDay)
)

function onDayChange(value) {
  emit('select-day', value === 'all' ? null : Number(value))
}
</script>

<template>
  <section id="map" class="result-section">
    <SectionHeader
      title="景点路线"
      description="高德地图 · 可拖拽缩放；生成按天路线后，地图只显示当前选中的那天。"
    />

    <div class="map-frame">
      <div v-if="routeDays.length" class="map-frame__tabs">
        <AppTabs
          :model-value="activeDayKey"
          :tabs="mapDayTabs"
          variant="instrument"
          aria-label="地图按天查看"
          @update:model-value="onDayChange"
        />
      </div>

      <TripMap
        :points="points"
        :hotels="hotels"
        :routes="routes"
        :fallback="mapUrl"
        :loading="loading"
        :error="error"
      />
    </div>

    <p class="sr-only">
      交互地图不能完全用键盘操作；景点名称、路线模式和估算说明同时列在下方的图例与“按天路线”中。
    </p>

    <div v-if="legendAttractions.length || legendItems.length || hasEstimated" class="map-legend">
      <span
        v-for="(item, index) in legendAttractions.slice(0, 8)"
        :key="item.name"
        class="map-legend__item"
      >
        <b>{{ mapLabel(index) }}</b>{{ item.name }}
      </span>
      <span v-for="item in legendItems" :key="`mode-${item.mode}`" class="map-legend__item">
        <i class="route-swatch" :style="{ background: item.color }"></i>{{ item.label }}
      </span>
      <span v-if="hasEstimated" class="map-legend__item">
        <i class="route-swatch route-swatch--estimated"></i>直线估算
      </span>
    </div>
  </section>
</template>

<style scoped>
.map-frame {
  position: relative;
  height: 360px;
  margin-top: 18px;
  overflow: hidden;
  background: var(--paper-deep);
  border: 1px solid var(--rule-strong);
  border-radius: var(--radius-md);
  box-shadow:
    inset 0 0 0 1px rgb(31 111 92 / 0.08),
    0 18px 44px rgb(31 51 45 / 0.12),
    var(--glow-accent);
}

.map-frame__tabs {
  position: absolute;
  top: 10px;
  left: 10px;
  z-index: 6;
  max-width: calc(100% - 150px);
}

.map-frame__tabs :deep(.tabs--instrument) {
  background: var(--paper-raised);
  box-shadow: 0 2px 10px rgb(31 51 45 / 0.14);
}

.map-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-top: 12px;
}

.map-legend__item {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: var(--type-micro);
}

.map-legend__item b {
  color: var(--ink);
  font-weight: 600;
}

.route-swatch {
  width: 16px;
  height: 2px;
  border-radius: 1px;
}

.route-swatch--estimated {
  background: repeating-linear-gradient(
    90deg,
    var(--text-faint) 0 4px,
    transparent 4px 7px
  );
}

@media (max-width: 760px) {
  .map-frame {
    height: 300px;
  }

  .map-frame__tabs {
    max-width: calc(100% - 172px);
  }

  .map-frame__tabs :deep(.tabs__key) {
    gap: 0;
    padding: 0 6px;
    font-size: 0.65rem;
  }

  .map-frame__tabs :deep(.tabs__dot) {
    display: none;
  }
}

@media (max-width: 520px) {
  .map-frame {
    height: 240px;
  }
}
</style>
