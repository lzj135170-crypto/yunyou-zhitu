<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from './Icon.vue'

defineProps({
  label: { type: String, default: '横向浏览' },
  prevLabel: { type: String, default: '向前浏览' },
  nextLabel: { type: String, default: '向后浏览' },
})

const track = ref(null)
const canPrev = ref(false)
const canNext = ref(false)
let resizeObserver = null
let mutationObserver = null

function updateNav() {
  const el = track.value
  if (!el) return
  canPrev.value = el.scrollLeft > 2
  canNext.value = el.scrollLeft + el.clientWidth < el.scrollWidth - 2
}

function scrollDir(direction) {
  const el = track.value
  if (!el) return
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  el.scrollBy({
    left: direction * Math.max(240, el.clientWidth * 0.82),
    behavior: reduce ? 'auto' : 'smooth',
  })
}

async function refresh() {
  await nextTick()
  updateNav()
}

async function scrollToStart() {
  await nextTick()
  const el = track.value
  if (!el) return
  el.scrollLeft = 0
  updateNav()
}

onMounted(() => {
  refresh()
  if (typeof ResizeObserver !== 'undefined' && track.value) {
    resizeObserver = new ResizeObserver(updateNav)
    resizeObserver.observe(track.value)
  }
  if (typeof MutationObserver !== 'undefined' && track.value) {
    mutationObserver = new MutationObserver(updateNav)
    mutationObserver.observe(track.value, { childList: true, subtree: true })
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  mutationObserver?.disconnect()
  mutationObserver = null
})

defineExpose({ refresh, scrollToStart })
</script>

<template>
  <div class="gallery-rail">
    <div
      ref="track"
      class="gallery-rail__track"
      role="region"
      :aria-label="label"
      tabindex="0"
      @scroll="updateNav"
      @keydown.left.prevent="scrollDir(-1)"
      @keydown.right.prevent="scrollDir(1)"
    >
      <slot />
    </div>

    <button
      v-if="canPrev"
      class="gallery-rail__nav gallery-rail__nav--prev"
      type="button"
      :aria-label="prevLabel"
      @click="scrollDir(-1)"
    >
      <Icon name="chevron-left" :size="18" />
    </button>
    <button
      v-if="canNext"
      class="gallery-rail__nav gallery-rail__nav--next"
      type="button"
      :aria-label="nextLabel"
      @click="scrollDir(1)"
    >
      <Icon name="chevron-right" :size="18" />
    </button>
  </div>
</template>

<style scoped>
.gallery-rail {
  position: relative;
  min-width: 0;
}

.gallery-rail__track {
  display: flex;
  gap: 14px;
  padding-bottom: 6px;
  overflow-x: auto;
  scroll-snap-type: x mandatory;
  scrollbar-width: none;
}

.gallery-rail__track::-webkit-scrollbar {
  display: none;
}

.gallery-rail__track:focus-visible {
  outline: 2px solid var(--patina-deep);
  outline-offset: 4px;
}

.gallery-rail__nav {
  position: absolute;
  top: 36%;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  color: var(--accent-deep);
  background: var(--paper-raised);
  border: 1px solid var(--accent-soft);
  border-radius: 50%;
  box-shadow: 0 6px 18px rgb(31 51 45 / 0.16);
}

.gallery-rail__nav:hover {
  color: var(--accent-strong);
  background: var(--paper-deep);
  border-color: var(--accent);
}

.gallery-rail__nav--prev {
  left: -8px;
}

.gallery-rail__nav--next {
  right: -8px;
}

@media (max-width: 760px) {
  .gallery-rail__nav {
    top: 34%;
    width: 30px;
    height: 30px;
  }

  .gallery-rail__nav--prev {
    left: 2px;
  }

  .gallery-rail__nav--next {
    right: 2px;
  }
}
</style>
