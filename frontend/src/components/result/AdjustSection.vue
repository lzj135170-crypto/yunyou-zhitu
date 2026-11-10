<script setup>
import AppButton from '../ui/AppButton.vue'
import AppTextarea from '../ui/AppTextarea.vue'
import SectionHeader from '../ui/SectionHeader.vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'submit'])
</script>

<template>
  <section id="adjust" class="result-section adjust-section">
    <SectionHeader
      title="智能调整"
      description="继续对话，行程会沿用当前会话的上下文和已查到的资料重新安排。"
    />

    <div class="adjust-form">
      <AppTextarea
        :model-value="modelValue"
        label="想怎么调整？"
        :rows="3"
        placeholder="例如：第二天想轻松一点，换成室内景点；预算再压低一些……"
        :disabled="loading"
        @update:model-value="emit('update:modelValue', $event)"
      />
      <div class="adjust-form__actions">
        <p>调整会重新跑一次行程编排，复杂行程可能需要 1–3 分钟。</p>
        <AppButton
          variant="primary"
          arrow
          :loading="loading"
          :disabled="!props.modelValue.trim()"
          @click="emit('submit')"
        >
          {{ loading ? '调整中…' : '提交调整' }}
        </AppButton>
      </div>
    </div>
  </section>
</template>

<style scoped>
.adjust-form {
  margin-top: 18px;
}

.adjust-form__actions {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
}

.adjust-form__actions p {
  color: var(--text-faint);
  font-size: var(--type-micro);
  line-height: 1.6;
}

@media (max-width: 640px) {
  .adjust-form__actions {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
