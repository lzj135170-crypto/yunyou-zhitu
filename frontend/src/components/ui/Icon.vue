<script setup>
import { computed } from 'vue'

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 20 },
  strokeWidth: { type: [Number, String], default: 1.6 },
  filled: { type: Boolean, default: false },
})

const ICONS = {
  compass: ['M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20', 'm16 8-2.5 5.5L8 16l2.5-5.5L16 8'],
  'map-pin': [
    'M12 21s7-6.1 7-11a7 7 0 1 0-14 0c0 4.9 7 11 7 11Z',
    'M12 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4',
  ],
  calendar: ['M4 5h16v16H4z', 'M8 3v4M16 3v4M4 10h16'],
  users: [
    'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2',
    'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z',
    'M22 21v-2a4 4 0 0 0-3-3.87',
    'M16 3.13a4 4 0 0 1 0 7.75',
  ],
  wallet: [
    'M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
    'M16 12h5',
    'M16 12a2 2 0 1 0 0 .01',
  ],
  sun: [
    'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10Z',
    'M12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4',
  ],
  'cloud-rain': [
    'M7 16a4 4 0 0 1 0-8 5 5 0 0 1 9.9 1A4 4 0 0 1 17 16',
    'M8 20v2M12 19v2M16 20v2',
  ],
  cloud: ['M7 18a5 5 0 0 1 0-10 6 6 0 0 1 11.4 1.6A4.2 4.2 0 0 1 18 18H7Z'],
  snow: [
    'M7 16a4 4 0 0 1 0-8 5 5 0 0 1 9.9 1A4 4 0 0 1 17 16',
    'M8 19v2M12 19v3M16 19v2',
    'M7 20l1-1M13 21l-1-1M17 20l-1-1',
  ],
  fog: [
    'M7 16a4 4 0 0 1 0-8 5 5 0 0 1 9.9 1A4 4 0 0 1 17 16',
    'M4 19h16M6 22h12',
  ],
  wind: ['M3 8h10a2 2 0 1 0-2-2', 'M3 12h14', 'M3 16h8a2 2 0 1 1-2 2'],
  utensils: [
    'M7 3v8a2 2 0 0 0 4 0V3',
    'M9 11v10',
    'M17 3v18',
    'M17 3c-2 0-3 2-3 5s1 4 3 4',
  ],
  hotel: ['M3 21V7l9-4 9 4v14', 'M9 21v-6h6v6', 'M7 10h.01M12 10h.01M17 10h.01'],
  train: ['M4 4h16v12H4z', 'M4 10h16', 'M8 20l2-4M16 20l-2-4'],
  plane: ['M2 12l20-8-7 18-3-7-10-3Z', 'M12 15l-4-3'],
  car: [
    'M5 17h14',
    'M6 17l1-5 2-3h6l2 3 1 5',
    'M8 13h8',
    'M7 17v2M17 17v2',
  ],
  bus: ['M4 5h16v12H4z', 'M4 9h16', 'M8 17v2M16 17v2', 'M7 13h.01M17 13h.01'],
  route: [
    'M6 19a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z',
    'M18 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z',
    'M9 16h4a4 4 0 0 0 4-4V8',
  ],
  book: ['M4 4a2 2 0 0 1 2-2h14v20H6a2 2 0 0 1-2-2z', 'M8 7h8M8 11h6'],
  sparkle: [
    'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z',
    'M19 16l.7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7L19 16Z',
  ],
  user: ['M20 21a8 8 0 0 0-16 0', 'M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10Z'],
  logout: [
    'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4',
    'm16 17 5-5-5-5',
    'M21 12H9',
  ],
  plus: ['M12 5v14M5 12h14'],
  download: ['M12 3v12', 'm7 10 5 5 5-5', 'M5 21h14'],
  printer: [
    'M6 9V3h12v6',
    'M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2',
    'M6 14h12v7H6z',
  ],
  refresh: ['M21 12a9 9 0 1 1-2.64-6.36', 'M21 3v6h-6'],
  trash: [
    'M3 6h18',
    'M8 6V4h8v2',
    'M19 6l-1 15H6L5 6',
    'M10 11v6M14 11v6',
  ],
  'chevron-right': ['m9 6 6 6-6 6'],
  'chevron-down': ['m6 9 6 6 6-6'],
  'chevron-left': ['m15 6-6 6 6 6'],
  'arrow-right': ['M4 12h16', 'm14 6 6 6-6 6'],
  'arrow-left': ['M20 12H4', 'm10 6-6 6 6 6'],
  check: ['m5 12 4 4L19 6'],
  alert: ['M12 3 2 21h20L12 3Z', 'M12 9v5', 'M12 18h.01'],
  clock: ['M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z', 'M12 7v5l3 2'],
  search: ['M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z', 'm21 21-4.3-4.3'],
  external: ['M14 4h6v6', 'M20 4 10 14', 'M20 14v6H4V4h6'],
  info: ['M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z', 'M12 11v6', 'M12 7h.01'],
  pencil: ['M4 20h4L20 8l-4-4L4 16v4Z', 'm13 5 4 4'],
  history: ['M3 12a9 9 0 1 0 3-6.7', 'M3 3v6h6', 'M12 7v5l3 2'],
  list: ['M8 6h13M8 12h13M8 18h13', 'M3 6h.01M3 12h.01M3 18h.01'],
  map: ['M3 6l6-3 6 3 6-3v15l-6 3-6-3-6 3V6Z', 'M9 3v15M15 6v15'],
  star: ['m12 3 2.7 5.5 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1L3.2 9.4l6.1-.9L12 3Z'],
  x: ['M6 6l12 12M18 6 6 18'],
  'arrow-up-right': ['M7 17 17 7', 'M7 7h10v10'],
}

const paths = computed(() => ICONS[props.name] || ICONS.info)
</script>

<template>
  <svg
    class="icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    :fill="filled ? 'currentColor' : 'none'"
    :stroke="filled ? 'none' : 'currentColor'"
    :stroke-width="strokeWidth"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path v-for="(d, index) in paths" :key="index" :d="d" />
  </svg>
</template>

<style scoped>
.icon {
  display: inline-block;
  flex: none;
  vertical-align: middle;
}
</style>
