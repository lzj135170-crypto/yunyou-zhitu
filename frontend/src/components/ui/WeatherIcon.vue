<script setup>
import { computed } from 'vue'
import Icon from './Icon.vue'

const props = defineProps({
  condition: { type: String, default: '' },
  size: { type: [Number, String], default: 18 },
})

const name = computed(() => {
  const text = String(props.condition || '')
  if (/雷/.test(text)) return 'cloud-rain'
  if (/雪|冰雹/.test(text)) return 'snow'
  if (/雨/.test(text)) return 'cloud-rain'
  if (/雾|霾/.test(text)) return 'fog'
  if (/风|沙/.test(text)) return 'wind'
  if (/阴|云/.test(text)) return 'cloud'
  return 'sun'
})
</script>

<template>
  <Icon :name="name" :size="size" class="weather-icon" />
</template>

<style scoped>
.weather-icon {
  color: var(--accent);
}
</style>
