<script setup>
import { ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'
import { useInView } from '../../composables/useInView'

const props = defineProps({
  item: { type: Object, required: true },
  photo: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  failed: { type: Boolean, default: false },
})

const emit = defineEmits(['load'])
const root = ref(null)
const broken = ref(false)
const inView = useInView(root, {
  threshold: 0.05,
  rootMargin: '180px',
  once: true,
})

watch(
  () => props.photo,
  () => {
    broken.value = false
  }
)

watch(
  inView,
  (value) => {
    if (!value || props.photo || props.failed) return
    emit('load', props.item)
  },
  { immediate: true }
)
</script>

<template>
  <div ref="root" class="food-photo">
    <img
      v-if="photo && !broken"
      :src="photo"
      :alt="`${item.name} 参考餐厅图`"
      loading="lazy"
      decoding="async"
      referrerpolicy="no-referrer"
      @error="broken = true"
    />
    <div v-else class="food-photo__empty">
      <Icon name="utensils" :size="20" />
      <span>{{ loading ? '正在找图…' : failed || broken ? '暂无参考图' : '参考餐厅图' }}</span>
    </div>
    <span v-if="photo && !broken" class="food-photo__credit">参考餐厅图 · 高德</span>
  </div>
</template>

<style scoped>
.food-photo {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 10;
  overflow: hidden;
  background:
    linear-gradient(135deg, var(--paper-deep), var(--instrument));
  border-bottom: 1px solid var(--rule);
}

.food-photo img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.food-photo__empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: center;
  justify-content: center;
  color: var(--text-faint);
  font-size: var(--type-micro);
}

.food-photo__credit {
  position: absolute;
  right: 8px;
  bottom: 8px;
  padding: 2px 6px;
  color: #fff;
  background: rgb(20 40 35 / 0.55);
  border-radius: 2px;
  font-size: 0.58rem;
  letter-spacing: 0.04em;
}
</style>
