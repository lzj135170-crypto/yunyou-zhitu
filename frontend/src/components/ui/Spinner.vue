<script setup>
defineProps({
  size: { type: [Number, String], default: 18 },
  label: { type: String, default: '' },
})
</script>

<template>
  <span
    class="spinner"
    :style="{ width: `${size}px`, height: `${size}px` }"
    :aria-label="label || undefined"
    :role="label ? 'status' : undefined"
  ></span>
</template>

<style scoped>
.spinner {
  display: inline-block;
  flex: none;
  border: 1.5px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: spin 700ms linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .spinner {
    animation: none;
  }
}
</style>
