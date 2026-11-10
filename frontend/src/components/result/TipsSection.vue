<script setup>
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'
import SectionHeader from '../ui/SectionHeader.vue'
import { splitBold } from '../../utils/inline'

const props = defineProps({
  tips: { type: Array, default: () => [] },
  warnings: { type: Array, default: () => [] },
})

// 后端已经清洗过；这里再挡一层，避免旧缓存或模型碎片把
// “旅行提示”渲染成 "':['" 这种无意义符号。
const visibleTips = computed(() =>
  props.tips.filter((tip) => {
    const text = String(tip || '').trim()
    return text.length > 1 && /[\p{L}\p{N}]/u.test(text)
  })
)
</script>

<template>
  <section id="tips" class="result-section">
    <SectionHeader
      title="旅行提示"
      description="行程里需要留意的地方；数据说明单独列出，避免和推荐混在一起。"
    />

    <ul v-if="visibleTips.length" class="tip-list">
      <li v-for="(tip, index) in visibleTips" :key="index">
        <Icon name="check" :size="14" />
        <span>
          <span
            v-for="(part, partIndex) in splitBold(tip)"
            :key="partIndex"
            :class="{ 'tip-strong': part.strong }"
          >{{ part.text }}</span>
        </span>
      </li>
    </ul>
    <p v-else class="note">这次没有额外提示。</p>

    <div v-if="warnings.length" class="warning-box">
      <div class="warning-box__head">
        <Icon name="alert" :size="16" />
        <strong>数据说明</strong>
      </div>
      <p v-for="(warning, index) in warnings" :key="index">{{ warning }}</p>
    </div>
  </section>
</template>

<style scoped>
.tip-list {
  margin-top: 18px;
  border-top: 1px solid var(--rule);
}

.tip-list li {
  display: flex;
  gap: 9px;
  align-items: flex-start;
  padding: 9px 0;
  color: var(--text);
  border-bottom: 1px solid var(--rule);
  font-size: var(--type-small);
  line-height: 1.7;
}

.tip-list svg {
  flex: none;
  margin-top: 3px;
  color: var(--patina-deep);
}

.tip-strong {
  color: var(--ink);
  font-weight: 650;
}

.warning-box {
  margin-top: 20px;
  padding: 14px 16px;
  background: var(--vermilion-soft);
  border: 1px solid rgb(192 73 61 / 0.26);
  border-radius: var(--radius-md);
}

.warning-box__head {
  display: flex;
  gap: 7px;
  align-items: center;
  color: var(--vermilion);
}

.warning-box__head strong {
  font-size: var(--type-small);
  font-weight: 650;
}

.warning-box p {
  margin-top: 5px;
  color: var(--text);
  font-size: var(--type-small);
  line-height: 1.7;
}

.note {
  margin-top: 16px;
  padding: 10px 12px;
  color: var(--text-muted);
  background: var(--paper-deep);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sm);
  font-size: var(--type-small);
}
</style>
