/**
 * 模型回复里偶尔会带 **加粗**。这里只拆出加粗片段，
 * 由模板用真实元素渲染，避免 v-html 和裸标记出现在页面上。
 */
export function splitBold(text) {
  return String(text || '')
    .split(/(\*\*[^*]+\*\*)/g)
    .filter(Boolean)
    .map((part) => {
      const strong = part.startsWith('**') && part.endsWith('**')
      return {
        strong,
        text: strong ? part.slice(2, -2) : part,
      }
    })
}
