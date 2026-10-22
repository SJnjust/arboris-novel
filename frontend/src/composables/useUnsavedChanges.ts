import { onMounted, onUnmounted } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { globalAlert } from '@/composables/useAlert'

export function useUnsavedChanges(dirty: () => boolean, message = '有尚未保存的修改。离开会丢失这些修改，确定继续吗？') {
  let pending: Promise<boolean> | undefined
  const confirmLeave = () => {
    if (!dirty()) return Promise.resolve(true)
    if (!pending) pending = globalAlert.showConfirm(message, '未保存的修改').finally(() => { pending = undefined })
    return pending
  }
  const beforeUnload = (event: BeforeUnloadEvent) => {
    if (!dirty()) return
    event.preventDefault()
    event.returnValue = ''
  }
  onBeforeRouteLeave(confirmLeave)
  onBeforeRouteUpdate(confirmLeave)
  onMounted(() => window.addEventListener('beforeunload', beforeUnload))
  onUnmounted(() => window.removeEventListener('beforeunload', beforeUnload))
  return { confirmLeave, beforeUnload }
}
