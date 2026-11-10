import { ref } from 'vue'

const toast = ref(null)
let timer = null

export function useToast() {
  function clearToast() {
    if (timer) clearTimeout(timer)
    timer = null
    toast.value = null
  }

  function showToast(message, type = 'error', duration = 3600) {
    if (!message) return
    clearToast()
    toast.value = { message: String(message), type }
    timer = setTimeout(() => {
      toast.value = null
      timer = null
    }, duration)
  }

  return { toast, showToast, clearToast }
}
