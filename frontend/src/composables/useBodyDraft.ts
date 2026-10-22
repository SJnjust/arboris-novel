import { ref, watch } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { globalAlert } from '@/composables/useAlert'

export function useBodyDraft() {
  const auth = useAuthStore()
  const key = ref('')
  let opening = 0
  const content = ref('')
  const original = ref('')
  const active = ref(false)
  let warned = false
  watch(content, value => {
    if (!active.value || !key.value) return
    try {
      if (value === original.value) localStorage.removeItem(key.value)
      else localStorage.setItem(key.value, JSON.stringify({ content: value, savedAt: Date.now() }))
    } catch {
      if (!warned) globalAlert.showError('本机草稿空间不足，修改仍保留在编辑框中，请及时保存。')
      warned = true
    }
  }, { flush: 'sync' })

  watch(() => auth.user?.id, () => {
    opening++
    active.value = false
    key.value = ''
    original.value = content.value = ''
  }, { flush: 'sync' })

  async function open(project: string, chapter: number, value: string) {
    const generation = ++opening
    const owner = auth.user?.id
    active.value = false
    key.value = owner ? `arboris:body-draft:${owner}:${project}:${chapter}` : ''
    original.value = content.value = value
    if (!owner) return false
    let draft: unknown
    try { draft = JSON.parse(localStorage.getItem(key.value) || 'null') } catch { /* 不能恢复损坏的草稿。 */ }
    if (draft && typeof draft === 'object' && 'content' in draft && typeof draft.content === 'string' && draft.content !== value) {
      const restore = await globalAlert.showConfirm('发现本章尚未正式保存的本机草稿，是否恢复？选择取消会继续使用服务端正文，原草稿保留到下一次编辑或正式保存。', '恢复正文草稿')
      if (generation !== opening || auth.user?.id !== owner) return false
      if (restore) content.value = draft.content
    }
    active.value = true
    return true
  }
  function saved() {
    try { if (key.value) localStorage.removeItem(key.value) } catch { /* 服务端保存成功不依赖本机存储权限。 */ }
    original.value = content.value
    active.value = false
  }
  function close() { active.value = false }
  return { content, original, active, open, saved, close }
}
