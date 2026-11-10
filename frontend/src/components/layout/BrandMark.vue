<script setup>
defineProps({
  size: { type: [Number, String], default: 28 },
  animated: { type: Boolean, default: false },
})
</script>

<template>
  <svg
    class="brand-mark"
    :class="{ 'brand-mark--animated': animated }"
    :width="size"
    :height="size"
    viewBox="0 0 32 32"
    fill="none"
    aria-hidden="true"
    focusable="false"
  >
    <!-- 两条交叉的路线：墨线在上，绿线在下，像织图的一根线。 -->
    <path
      d="M4 24c6 0 6-16 12-16s6 16 12 16"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
      pathLength="1"
    />
    <path
      d="M4 8c6 0 6 16 12 16s6-16 12-16"
      stroke="var(--accent)"
      stroke-width="1.8"
      stroke-linecap="round"
      pathLength="1"
    />
  </svg>
</template>

<style scoped>
.brand-mark {
  display: block;
  flex: none;
}

.brand-mark--animated path {
  stroke-dasharray: 1;
  stroke-dashoffset: 1;
  animation: brand-draw 900ms var(--ease-out) forwards;
}

.brand-mark--animated path + path {
  animation-delay: 120ms;
}

@keyframes brand-draw {
  to {
    stroke-dashoffset: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .brand-mark--animated path {
    animation: none;
    stroke-dashoffset: 0;
  }
}
</style>
