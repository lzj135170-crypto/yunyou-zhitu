<script setup>
import EmptyState from '../ui/EmptyState.vue'
import Icon from '../ui/Icon.vue'
import SectionHeader from '../ui/SectionHeader.vue'
import { isHttpUrl } from '../../utils/format'

defineProps({
  sources: { type: Array, default: () => [] },
})
</script>

<template>
  <section id="sources" class="result-section">
    <SectionHeader
      title="参考资料"
      description="最终回复里可回溯的知识库出处；没有出现《标题》的回答不会列在这里。"
    >
      <template v-if="sources.length" #actions>
        <span class="source-count mono">EVIDENCE / {{ sources.length }} 条</span>
      </template>
    </SectionHeader>

    <ul v-if="sources.length" class="source-list">
      <li v-for="item in sources" :key="item.title">
        <div class="source-list__main">
          <span class="source-list__title">《{{ item.title }}》</span>
          <span v-if="item.section && item.section.length" class="source-list__section">
            {{ item.section.join(' / ') }}
          </span>
        </div>
        <a
          v-if="isHttpUrl(item.source)"
          class="source-list__link"
          :href="item.source"
          target="_blank"
          rel="noopener noreferrer"
        >
          原文
          <Icon name="arrow-up-right" :size="13" />
        </a>
      </li>
    </ul>

    <EmptyState
      v-else
      icon="book"
      title="这次回复没有引用知识库资料"
      description="回答里的价格、时刻等数字会按估算处理，不写成确定事实。"
    />
  </section>
</template>

<style scoped>
.source-count {
  color: var(--accent-deep);
  font-size: 0.58rem;
  letter-spacing: 0.08em;
  white-space: nowrap;
}

.source-list {
  margin-top: 18px;
  border-top: 1px solid var(--rule);
}

.source-list li {
  display: flex;
  gap: 16px;
  align-items: baseline;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid var(--rule);
}

.source-list__main {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;
  min-width: 0;
}

.source-list__title {
  color: var(--ink);
  font-size: var(--type-small);
  font-weight: 600;
}

.source-list__section {
  color: var(--text-muted);
  font-size: var(--type-label);
}

.source-list__link {
  display: inline-flex;
  flex: none;
  gap: 3px;
  align-items: center;
  color: var(--patina-deep);
  font-size: var(--type-label);
  text-decoration: none;
}

.source-list__link:hover {
  text-decoration: underline;
}
</style>
