<script setup>
import { computed, ref } from 'vue'
import { useInView } from '../../composables/useInView'
import { usePrefersReducedMotion } from '../../composables/usePrefersReducedMotion'

const props = defineProps({
  points: { type: Array, default: () => [] },
  height: { type: Number, default: 36 },
})

const root = ref(null)
const reduced = usePrefersReducedMotion()
const inView = useInView(root, { threshold: 0.3, once: true })
const visible = computed(() => reduced.value || inView.value)

const WIDTH = 240
const PADDING = 4

const values = computed(() =>
  props.points.map((value) => Number(value)).filter((value) => Number.isFinite(value))
)

const coordinates = computed(() => {
  const list = values.value
  if (list.length < 2) return []
  const min = Math.min(...list)
  const max = Math.max(...list)
  const span = Math.max(1, max - min)
  return list.map((value, index) => ({
    x: (index / (list.length - 1)) * WIDTH,
    y: PADDING + ((max - value) / span) * (props.height - PADDING * 2),
  }))
})

const linePath = computed(() => {
  if (!coordinates.value.length) return ''
  return coordinates.value
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x.toFixed(1)},${point.y.toFixed(1)}`)
    .join(' ')
})

const areaPath = computed(() => {
  if (!coordinates.value.length) return ''
  const first = coordinates.value[0]
  const last = coordinates.value[coordinates.value.length - 1]
  return `${linePath.value} L${last.x.toFixed(1)},${props.height} L${first.x.toFixed(1)},${props.height} Z`
})

const lastPoint = computed(() => coordinates.value[coordinates.value.length - 1] || null)
</script>

<template>
  <svg
    ref="root"
    class="sparkline"
    :viewBox="`0 0 ${WIDTH} ${height}`"
    :height="height"
    preserveAspectRatio="none"
    aria-hidden="true"
    focusable="false"
  >
    <path v-if="areaPath" class="sparkline__area" :d="areaPath" />
    <path
      v-if="linePath"
      class="sparkline__line"
      :d="linePath"
      pathLength="1"
      :style="{ strokeDashoffset: visible ? 0 : 1 }"
    />
    <circle
      v-if="lastPoint"
      class="sparkline__dot"
      :cx="lastPoint.x"
      :cy="lastPoint.y"
      r="2.2"
    />
  </svg>
</template>

<style scoped>
.sparkline {
  display: block;
  width: 100%;
  overflow: visible;
}

.sparkline__area {
  fill: rgb(31 111 92 / 0.08);
}

.sparkline__line {
  fill: none;
  stroke: var(--accent);
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-dasharray: 1;
  transition: stroke-dashoffset 600ms var(--ease-out);
  vector-effect: non-scaling-stroke;
}

.sparkline__dot {
  fill: var(--accent);
}

@media (prefers-reduced-motion: reduce) {
  .sparkline__line {
    transition: none;
  }
}
</style>
