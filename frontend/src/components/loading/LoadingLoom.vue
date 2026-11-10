<script setup>
import { computed } from 'vue'
import BrandMark from '../layout/BrandMark.vue'

const props = defineProps({
  mode: { type: String, default: 'create' },
  seconds: { type: Number, default: 0 },
})

const title = computed(() =>
  props.mode === 'adjust' ? '正在调整行程' : '正在为你织行程'
)
</script>

<template>
  <div class="loading-screen" role="status" aria-live="polite" aria-busy="true">
    <div class="loading-screen__card">
      <div class="loading-loom" aria-hidden="true">
        <svg viewBox="0 0 220 120" class="loading-loom__svg">
          <path
            class="loading-loom__line loading-loom__line--accent"
            d="M8 68 C42 16 76 108 110 56 S178 12 212 58"
            pathLength="1"
          />
          <path
            class="loading-loom__line loading-loom__line--patina"
            d="M8 52 C42 104 76 12 110 64 S178 108 212 50"
            pathLength="1"
          />
        </svg>
        <BrandMark :size="30" class="loading-loom__mark" />
      </div>

      <strong>{{ title }}</strong>
      <p>多智能体正在依次工作，复杂行程可能需要 1–3 分钟。</p>
      <span class="loading-screen__seconds num" aria-hidden="true">{{ seconds }}s</span>
    </div>
  </div>
</template>

<style scoped>
.loading-screen {
  position: fixed;
  inset: 0;
  z-index: 80;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--loading-scrim);
  backdrop-filter: blur(3px);
}

.loading-screen__card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  width: min(400px, 100%);
  padding: 26px 24px 24px;
  text-align: center;
  background: var(--paper-raised);
  border: 1px solid var(--rule-strong);
  border-radius: var(--radius-md);
  box-shadow: var(--lift-dialog);
}

.loading-loom {
  position: relative;
  width: 220px;
  max-width: 100%;
  height: 120px;
}

.loading-loom__svg {
  display: block;
  width: 100%;
  height: 100%;
  overflow: visible;
}

.loading-loom__line {
  fill: none;
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-dasharray: 0.34 1;
  animation: loom-weave 2.4s linear infinite;
}

.loading-loom__line--accent {
  stroke: var(--accent);
}

.loading-loom__line--patina {
  stroke: var(--patina);
  animation-direction: reverse;
  animation-delay: -1.2s;
}

.loading-loom__mark {
  position: absolute;
  top: 50%;
  left: 50%;
  color: var(--ink);
  transform: translate(-50%, -50%);
}

.loading-screen__card strong {
  color: var(--ink);
  font-family: var(--font-ui);
  font-size: 1.05rem;
  font-weight: 650;
}

.loading-screen__card p {
  max-width: 36ch;
  color: var(--text-muted);
  font-size: var(--type-small);
  line-height: 1.7;
}

.loading-screen__seconds {
  margin-top: 2px;
  color: var(--text-faint);
  font-family: var(--font-mono);
  font-size: var(--type-label);
}

@keyframes loom-weave {
  to {
    stroke-dashoffset: -1.34;
  }
}

@media (prefers-reduced-motion: reduce) {
  .loading-loom__line {
    animation: none;
    stroke-dasharray: none;
  }
}
</style>
