<script setup>
import Skeleton from '../ui/Skeleton.vue'
</script>

<template>
  <div class="result-skeleton" role="status" aria-live="polite" aria-busy="true">
    <span class="sr-only">正在加载行程</span>

    <div class="result-skeleton__hero">
      <div class="result-skeleton__hero-copy">
        <Skeleton width="22%" height="11px" />
        <Skeleton width="68%" height="42px" />
        <Skeleton width="42%" height="12px" />
        <Skeleton width="96%" height="12px" />
        <Skeleton width="88%" height="12px" />
        <Skeleton width="72%" height="12px" />
      </div>
      <Skeleton width="100%" height="220px" radius="var(--radius-md)" />
    </div>

    <div class="result-skeleton__stats">
      <div v-for="index in 4" :key="index" class="result-skeleton__stat">
        <Skeleton width="48%" height="10px" />
        <Skeleton width="64%" height="20px" />
      </div>
    </div>

    <div class="result-skeleton__grid">
      <div class="result-skeleton__panel">
        <Skeleton width="24%" height="16px" />
        <Skeleton width="100%" height="280px" radius="var(--radius-md)" />
      </div>
      <div class="result-skeleton__panel">
        <Skeleton width="38%" height="16px" />
        <Skeleton width="100%" height="92px" radius="var(--radius-sm)" />
        <Skeleton width="100%" height="12px" />
        <Skeleton width="82%" height="12px" />
        <Skeleton width="64%" height="12px" />
      </div>
    </div>

    <div class="result-skeleton__timeline">
      <Skeleton width="20%" height="16px" />
      <div v-for="index in 3" :key="index" class="result-skeleton__day">
        <span class="result-skeleton__node"></span>
        <div class="result-skeleton__day-body">
          <Skeleton width="26%" height="15px" />
          <Skeleton width="100%" height="12px" />
          <Skeleton width="72%" height="12px" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.result-skeleton {
  padding-top: 20px;
}

.result-skeleton__hero {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(260px, 0.75fr);
  gap: clamp(20px, 3vw, 40px);
  align-items: stretch;
  padding-bottom: 22px;
  border-bottom: 1px solid var(--rule);
}

.result-skeleton__hero-copy {
  display: flex;
  flex-direction: column;
  gap: 10px;
  justify-content: center;
}

.result-skeleton__stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin-top: 18px;
  padding: 14px 16px;
  background: var(--paper-raised);
  border: 1px solid var(--rule);
  border-radius: var(--radius-md);
}

.result-skeleton__stat {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0 16px;
  border-left: 1px solid var(--rule);
}

.result-skeleton__stat:first-child {
  padding-left: 0;
  border-left: 0;
}

.result-skeleton__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: clamp(16px, 2.2vw, 32px);
  align-items: stretch;
  margin-top: 24px;
}

.result-skeleton__panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 18px;
  background: var(--paper-raised);
  border: 1px solid var(--rule);
  border-radius: var(--radius-md);
}

.result-skeleton__timeline {
  position: relative;
  margin-top: 24px;
  padding: 18px 0 0 24px;
  border-left: 1px solid var(--accent-strong);
}

.result-skeleton__day {
  display: flex;
  gap: 16px;
  padding-top: 18px;
}

.result-skeleton__node {
  flex: none;
  width: 7px;
  height: 7px;
  margin-top: 5px;
  margin-left: -28px;
  background: var(--accent);
  border: 3px solid var(--paper);
  border-radius: 50%;
}

.result-skeleton__day-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

@media (max-width: 760px) {
  .result-skeleton__hero {
    grid-template-columns: 1fr;
  }

  .result-skeleton__stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    row-gap: 14px;
  }

  .result-skeleton__stat:nth-child(odd) {
    padding-left: 0;
    border-left: 0;
  }

  .result-skeleton__stat:nth-child(n + 3) {
    padding-top: 14px;
    border-top: 1px solid var(--rule);
  }

  .result-skeleton__grid {
    grid-template-columns: 1fr;
  }
}
</style>
