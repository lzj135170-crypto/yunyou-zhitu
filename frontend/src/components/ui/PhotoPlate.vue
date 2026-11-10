<script setup>
import { computed } from 'vue'
import { useImageFailures } from '../../composables/useImageFailures'

const props = defineProps({
  src: { type: String, default: '' },
  alt: { type: String, default: '' },
  label: { type: String, default: '' },
  eager: { type: Boolean, default: false },
  ratio: { type: String, default: '' },
})

const emit = defineEmits(['error'])
const { hasFailed, markFailed } = useImageFailures()

const showImage = computed(() => Boolean(props.src) && !hasFailed(props.src))
const initial = computed(() => {
  const text = String(props.label || props.alt || '旅').trim()
  return text ? text.slice(0, 1) : '旅'
})

function onError(event) {
  markFailed(props.src)
  emit('error', event)
}
</script>

<template>
  <div
    class="photo-plate"
    :class="{ 'is-fallback': !showImage }"
    :style="ratio ? { '--photo-ratio': ratio } : undefined"
  >
    <img
      v-if="showImage"
      class="photo-plate__img"
      :src="src"
      :alt="alt"
      :loading="eager ? 'eager' : 'lazy'"
      :fetchpriority="eager ? 'high' : undefined"
      decoding="async"
      referrerpolicy="no-referrer"
      @error="onError"
    />
    <div v-else class="photo-plate__fallback" aria-hidden="true">
      <span class="photo-plate__initial">{{ initial }}</span>
      <span class="photo-plate__rule"></span>
      <span class="photo-plate__hint">暂无照片</span>
    </div>
  </div>
</template>

<style scoped>
.photo-plate {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 120px;
  overflow: hidden;
  background: var(--paper-deep);
  border: 1px solid var(--rule-strong);
  border-radius: var(--radius-md);
}

.photo-plate[style*="--photo-ratio"] {
  aspect-ratio: var(--photo-ratio);
}

.photo-plate__img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.photo-plate__fallback {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 9px;
  background:
    radial-gradient(120% 90% at 20% 0%, rgb(220 239 243 / 0.9), transparent 62%),
    linear-gradient(145deg, var(--paper-deep), var(--paper-raised));
}

.photo-plate__initial {
  color: var(--accent);
  font-family: var(--font-display);
  font-size: clamp(2.8rem, 6vw, 4.6rem);
  font-weight: 300;
  line-height: 1;
}

.photo-plate__rule {
  width: 42px;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
}

.photo-plate__hint {
  color: var(--text-faint);
  font-family: var(--font-mono);
  font-size: var(--type-micro);
  letter-spacing: 0.08em;
}
</style>
