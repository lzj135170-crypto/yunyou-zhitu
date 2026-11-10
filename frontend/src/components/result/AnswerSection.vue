<script setup>
import Icon from '../ui/Icon.vue'
import SectionHeader from '../ui/SectionHeader.vue'

defineProps({
  answer: { type: String, default: '' },
  showAnswer: { type: Boolean, default: false },
})

const emit = defineEmits(['toggle'])
</script>

<template>
  <section id="answer" class="result-section">
    <SectionHeader
      title="完整回复"
      description="模型按资料整理后的完整文字；数字与出处以正文为准。"
    />

    <button class="answer-toggle" type="button" :aria-expanded="showAnswer" @click="emit('toggle')">
      <span>{{ showAnswer ? '收起完整回复' : '展开完整回复' }}</span>
      <Icon :name="showAnswer ? 'chevron-down' : 'chevron-right'" :size="16" />
    </button>

    <!-- 始终保留在 DOM，屏幕下由 showAnswer 控制；打印时 CSS 强制显示。 -->
    <div class="answer-text" :class="{ 'is-visible': showAnswer }">
      {{ answer || '这次没有可展示的完整回复。' }}
    </div>
  </section>
</template>

<style scoped>
.answer-toggle {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  margin-top: 14px;
  padding: 11px 0;
  color: var(--text-muted);
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--rule);
  font-size: var(--type-small);
  text-align: left;
}

.answer-toggle:hover {
  color: var(--ink);
}

.answer-text {
  display: none;
  max-width: 72ch;
  margin-top: 16px;
  color: var(--text);
  font-size: var(--type-small);
  line-height: 1.9;
  white-space: pre-wrap;
}

.answer-text.is-visible {
  display: block;
}
</style>
