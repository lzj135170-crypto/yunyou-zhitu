<script setup>
import { useScrollProgress } from '../../composables/useScrollProgress'

const { progress } = useScrollProgress()
</script>

<template>
  <div class="scroll-progress" aria-hidden="true">
    <span :style="{ transform: `scaleX(${progress})` }"></span>
  </div>
</template>

<style scoped>
.scroll-progress {
  position: fixed;
  top: 0;
  right: 0;
  left: 0;
  z-index: 60;
  height: 1px;
  overflow: hidden;
  pointer-events: none;
}

.scroll-progress span {
  display: block;
  width: 100%;
  height: 100%;
  background: linear-gradient(90deg, var(--accent-deep), var(--accent), var(--patina));
  box-shadow: 0 0 10px rgb(31 111 92 / 0.32);
  transform-origin: left center;
}
</style>
