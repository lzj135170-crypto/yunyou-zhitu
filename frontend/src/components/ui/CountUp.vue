<script setup>
import { computed, ref } from 'vue'
import { useCountUp } from '../../composables/useCountUp'
import { useInView } from '../../composables/useInView'
import { usePrefersReducedMotion } from '../../composables/usePrefersReducedMotion'

const props = defineProps({
  value: { type: Number, default: 0 },
  prefix: { type: String, default: '' },
  suffix: { type: String, default: '' },
  decimals: { type: Number, default: 0 },
  duration: { type: Number, default: 720 },
  label: { type: String, default: '' },
})

const root = ref(null)
const reduced = usePrefersReducedMotion()
const inView = useInView(root, { threshold: 0.35, once: true })
const active = computed(() => inView.value && !reduced.value)
const { display } = useCountUp(() => props.value, {
  active,
  duration: props.duration,
})

function format(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '0'
  return number.toLocaleString('zh-CN', {
    minimumFractionDigits: props.decimals,
    maximumFractionDigits: props.decimals,
  })
}

const finalText = computed(() => format(props.value))
const visual = computed(() => (reduced.value ? finalText.value : format(display.value)))
const ariaLabel = computed(
  () => props.label || `${props.prefix}${finalText.value}${props.suffix}`
)
</script>

<template>
  <span ref="root" class="count-up">
    <span class="sr-only">{{ ariaLabel }}</span>
    <span class="count-up__visual" aria-hidden="true">
      {{ prefix }}{{ visual }}<span v-if="suffix" class="count-up__suffix">{{ suffix }}</span>
    </span>
  </span>
</template>

<style scoped>
.count-up {
  display: inline-flex;
  align-items: baseline;
  min-width: 0;
}

.count-up__visual {
  display: inline-flex;
  align-items: baseline;
  font-variant-numeric: tabular-nums;
}

.count-up__suffix {
  margin-left: 3px;
  color: var(--text-muted);
  font-family: var(--font-ui);
  font-size: var(--type-micro);
}
</style>
