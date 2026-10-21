import { computed, onScopeDispose, ref, watch } from 'vue'
import { ApiError, NovelAPI, type AIJob, type JobSubmitted } from '@/api/novel'

const active = (job: AIJob) => job.status === 'queued' || job.status === 'running'

interface Options {
  kind?: string
  onTerminal?: (job: AIJob, signal: AbortSignal) => Promise<void> | void
  onRecovered?: (jobs: AIJob[]) => void
}

export function useJob(projectId: () => string | undefined, options: Options = {}) {
  const jobs = ref<AIJob[]>([])
  const error = ref<string | null>(null)
  const cancelling = ref(false)
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  const requests = new Set<AbortController>()
  const inFlight = new Map<string, AbortController>()
  const failures = new Map<string, number>()
  const ids = new Set<string>()
  const pending: Record<string, string> = {}
  const kind = options.kind || 'chapter_generate'
  let epoch = 0
  let disposed = false
  const storageKey = () => `arboris:jobs:${projectId()}:${kind}`
  const visible = () => typeof document === 'undefined' || !document.hidden
  const activeJobs = computed(() => jobs.value.filter(active))

  function save() {
    if (!projectId()) return
    try { localStorage.setItem(storageKey(), JSON.stringify({ ids: [...ids], pending })) } catch { /* 隐私模式仍可在当前页跟踪任务。 */ }
  }

  function merge(job: AIJob): AIJob | undefined {
    if (job.project_id !== projectId() || job.kind !== kind) return
    const index = jobs.value.findIndex(item => item.id === job.id)
    const previous = jobs.value[index]
    if (previous && (!active(previous) && active(job) || previous.updated_at > job.updated_at)) return previous
    if (index >= 0) jobs.value[index] = job
    else jobs.value.unshift(job)
    jobs.value = jobs.value.filter(active).sort((a, b) => b.created_at.localeCompare(a.created_at))
      .concat(jobs.value.filter(item => !active(item)).sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 20))
    return job
  }

  function stop() {
    for (const timer of timers.values()) clearTimeout(timer)
    timers.clear()
    for (const controller of requests) controller.abort()
    requests.clear()
    inFlight.clear()
  }

  function schedule(id: string, interval: number, token: number) {
    if (disposed || token !== epoch || !visible()) return
    const previous = timers.get(id)
    if (previous) clearTimeout(previous)
    timers.set(id, setTimeout(() => { timers.delete(id); void poll(id, Math.min(interval * 1.5, 8000), token) }, interval))
  }

  async function poll(id: string, interval = 1500, token = epoch) {
    if (disposed || token !== epoch || !visible() || inFlight.has(id)) return
    const controller = new AbortController()
    requests.add(controller)
    inFlight.set(id, controller)
    try {
      const received = await NovelAPI.getJob(id, { signal: controller.signal })
      if (disposed || token !== epoch || controller.signal.aborted) return
      if (received.project_id !== projectId() || received.kind !== kind) { ids.delete(id); save(); return }
      const job = merge(received)!
      if (active(job)) { failures.delete(id); schedule(id, interval, token) }
      else {
        await options.onTerminal?.(job, controller.signal)
        if (disposed || token !== epoch || controller.signal.aborted) return
        failures.delete(id)
        ids.delete(id)
        save()
      }
    } catch (reason) {
      if (disposed || token !== epoch || controller.signal.aborted) return
      if (reason instanceof ApiError && reason.status === 404) { ids.delete(id); save(); return }
      const count = (failures.get(id) || 0) + 1
      failures.set(id, count)
      if (count <= 3) schedule(id, interval, token)
      else error.value = reason instanceof Error ? reason.message : '任务状态暂时不可用，请重试'
    } finally {
      requests.delete(controller)
      if (inFlight.get(id) === controller) inFlight.delete(id)
    }
  }

  async function recover() {
    const project = projectId()
    if (!project || disposed) return
    const token = ++epoch
    stop()
    ids.clear()
    for (const key of Object.keys(pending)) delete pending[key]
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey()) || '{}')
      for (const id of saved.ids || []) if (typeof id === 'string') ids.add(id)
      for (const [target, key] of Object.entries(saved.pending || {})) if (typeof key === 'string') pending[target] = key
    } catch { /* 损坏的本地记录不影响服务端恢复。 */ }
    if (!visible()) return
    const controller = new AbortController()
    requests.add(controller)
    error.value = null
    try {
      const restored = await NovelAPI.getJobs(project, { signal: controller.signal })
      if (disposed || token !== epoch) return
      const matching = restored.filter(job => job.kind === kind)
      for (const job of matching) {
        ids.add(job.id); merge(job)
        delete pending[job.target]
      }
      save()
      options.onRecovered?.(matching)
    } catch (reason) {
      if (!controller.signal.aborted && token === epoch && !disposed) error.value = reason instanceof Error ? reason.message : '恢复任务失败，请重试'
    } finally { requests.delete(controller) }
    if (token === epoch && !disposed) for (const id of ids) void poll(id, 1500, token)
  }

  async function submit(target: string, create: (key: string, signal: AbortSignal) => Promise<JobSubmitted>) {
    if (!projectId()) throw new Error('没有当前项目')
    const token = epoch
    const controller = new AbortController()
    requests.add(controller)
    const key = pending[target] || crypto.randomUUID()
    pending[target] = key
    save()
    try {
      let id: string
      try { id = (await create(key, controller.signal)).job_id }
      catch (reason) {
        if (reason instanceof ApiError && reason.status === 409 && reason.jobId) id = reason.jobId
        else throw reason
      }
      if (disposed || token !== epoch) return
      delete pending[target]
      ids.add(id); save()
      failures.delete(id)
      await poll(id, 1500, token)
      return id
    } catch (reason) {
      if (controller.signal.aborted && (disposed || token !== epoch || !visible())) return
      throw reason
    } finally { requests.delete(controller) }
  }

  async function cancel(id: string) {
    cancelling.value = true
    const token = epoch
    const controller = new AbortController()
    requests.add(controller)
    try {
      const job = await NovelAPI.cancelJob(id, { signal: controller.signal })
      if (disposed || token !== epoch) return
      inFlight.get(id)?.abort()
      inFlight.delete(id)
      const timer = timers.get(id)
      if (timer) clearTimeout(timer)
      timers.delete(id)
      merge(job)
      await poll(id)
    } catch (reason) {
      if (controller.signal.aborted && (disposed || token !== epoch || !visible())) return
      throw reason
    } finally { requests.delete(controller); cancelling.value = false }
  }

  function visibility() {
    if (!visible()) stop()
    else { failures.clear(); void recover() }
  }

  watch(projectId, () => { epoch++; stop(); ids.clear(); jobs.value = []; void recover() }, { immediate: true })
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', visibility)
  onScopeDispose(() => {
    disposed = true; epoch++; stop()
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', visibility)
  })
  return { jobs, activeJobs, error, cancelling, submit, cancel, recover }
}
