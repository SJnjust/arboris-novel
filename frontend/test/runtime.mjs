import { readFile } from 'node:fs/promises'
import { createContext, SourceTextModule, SyntheticModule } from 'node:vm'
import { webcrypto } from 'node:crypto'
import { setImmediate } from 'node:timers/promises'
import ts from 'typescript'
import * as vue from 'vue'
import * as pinia from 'pinia'
import { marked } from 'marked'

export async function settle() {
  for (let count = 0; count < 8; count++) await setImmediate()
}

export class Clock {
  now = 0
  sequence = 0
  timers = new Map()
  set = (fn, delay) => {
    const id = ++this.sequence
    this.timers.set(id, { fn, delay, due: this.now + delay })
    return id
  }
  clear = id => this.timers.delete(id)
  nextDelay() {
    return Math.min(...[...this.timers.values()].map(item => item.due - this.now))
  }
  async next() {
    const [id, timer] = [...this.timers.entries()].sort((a, b) => a[1].due - b[1].due)[0]
    this.now = timer.due
    this.timers.delete(id)
    timer.fn()
    await settle()
  }
}

class Document extends EventTarget {
  hidden = false
  body = { classList: { add() {}, remove() {} } }
  listeners = new Set()
  addEventListener(name, fn) { super.addEventListener(name, fn); this.listeners.add(fn) }
  removeEventListener(name, fn) { super.removeEventListener(name, fn); this.listeners.delete(fn) }
  hide(value) { this.hidden = value; this.dispatchEvent(new Event('visibilitychange')) }
}

export function storage() {
  const values = new Map()
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }
}

export function job(status = 'running', id = 'job-1') {
  return { id, project_id: 'project-1', kind: 'chapter_generate', target: '1', status,
    stage: status === 'running' ? 'versions' : status, progress: status === 'succeeded' ? 100 : 45,
    payload: { chapter_number: 1 }, result: status === 'succeeded' ? { version_count: 2 } : null,
    error: null, created_at: '2026-10-21T00:00:00', updated_at: '2026-10-21T00:00:01', heartbeat_at: '2026-10-21T00:00:01' }
}

export async function runtime(fetcher, saved = storage()) {
  const clock = new Clock()
  const document = new Document()
  const window = new EventTarget()
  const leaveGuards = []
  const updateGuards = []
  let alertHandlers = {}
  const alertsProxy = new Proxy({}, { get: (_, key) => alertHandlers[key] })
  const calls = []
  const redirects = []
  const auth = vue.reactive({ isAuthenticated: true, token: 'offline-fixture', user: { id: 1 }, logout() { this.isAuthenticated = false; this.user = null } })
  const context = createContext({ console, Error, Headers, Response, FormData, AbortController, URL, DOMException, TextDecoder,
    crypto: webcrypto, document, window, localStorage: saved, setTimeout: clock.set, clearTimeout: clock.clear,
    fetch: (url, options) => { calls.push({ url, options }); return fetcher(String(url), options) } })
  const synthetic = (values) => new SyntheticModule(Object.keys(values), function () {
    for (const [name, value] of Object.entries(values)) this.setExport(name, value)
  }, { context })
  const modules = new Map([
    ['vue', synthetic(vue)],
    ['vue-router', synthetic({ onBeforeRouteLeave: fn => leaveGuards.push(fn), onBeforeRouteUpdate: fn => updateGuards.push(fn) })],
    ['@/composables/useAlert', synthetic({ globalAlert: alertsProxy })],
    ['@/stores/auth', synthetic({ useAuthStore: () => auth })],
    ['@/router', synthetic({ default: { push: path => redirects.push(path) } })]
  ])
  async function sourceModule(name, path, suffix = '') {
    const source = await readFile(new URL(path, import.meta.url), 'utf8')
    const script = path.endsWith('.vue') ? source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1] : source
    const code = ts.transpileModule(`${script}\n${suffix}`, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
    return new SourceTextModule(code, { context, identifier: name, initializeImportMeta: meta => { meta.env = { MODE: 'production' } } })
  }
  for (const [name, path] of [['@/utils/errors', '../src/utils/errors.ts'], ['@/composables/useUnsavedChanges', '../src/composables/useUnsavedChanges.ts'], ['@/composables/useBodyDraft', '../src/composables/useBodyDraft.ts'], ['@/api/novel', '../src/api/novel.ts'], ['@/utils/volumes', '../src/utils/volumes.ts'], ['useJob', '../src/composables/useJob.ts'], ['@/composables/useVolumeJobs', '../src/composables/useVolumeJobs.ts']]) {
    const source = await readFile(new URL(path, import.meta.url), 'utf8')
    const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
    const module = new SourceTextModule(code, { context, identifier: name, initializeImportMeta: meta => { meta.env = { MODE: 'production' } } })
    modules.set(name, module)
    await module.link(specifier => {
      if (!modules.has(specifier)) throw new Error(`Unmocked import: ${specifier}`)
      return modules.get(specifier)
    })
    await module.evaluate()
    if (name === 'useJob') {
      modules.set('@/composables/useJob', module)
      modules.set('./useJob', module)
    }
  }
  return { api: modules.get('@/api/novel').namespace, useJob: modules.get('useJob').namespace.useJob,
    useVolumeJobs: modules.get('@/composables/useVolumeJobs').namespace.useVolumeJobs,
    clock, calls, document, window, leaveGuards, updateGuards, storage: saved, redirects, auth, vue, volumes: modules.get('@/utils/volumes').namespace,
    errors: modules.get('@/utils/errors').namespace,
    drafts: modules.get('@/composables/useBodyDraft').namespace,
    guards: modules.get('@/composables/useUnsavedChanges').namespace,
    setAlerts(value) { alertHandlers = value },
    async novelStore() {
      pinia.setActivePinia(pinia.createPinia())
      modules.set('pinia', synthetic(pinia))
      const module = await sourceModule('@/stores/novel', '../src/stores/novel.ts')
      await module.link(specifier => modules.get(specifier))
      await module.evaluate()
      return module.namespace.useNovelStore()
    },
    async component(path, names, store, props = {}, alerts = {}) {
      alertHandlers = alerts
      context.defineProps = () => props
      const emitted = []
      context.defineEmits = () => (...args) => emitted.push(args)
      modules.set('@/stores/novel', synthetic({ useNovelStore: () => store }))
      modules.set('vue-router', synthetic({ useRouter: () => ({ push: path => redirects.push(path), replace: path => redirects.push(path) }), useRoute: () => ({ query: {}, params: { id: 'project-1' } }), onBeforeRouteLeave: fn => leaveGuards.push(fn), onBeforeRouteUpdate: fn => updateGuards.push(fn) }))
      modules.set('@/composables/useAlert', synthetic({ globalAlert: alerts }))
      modules.set('marked', synthetic({ marked }))
      const mounted = []
      const unmounted = []
      const componentVue = synthetic({ ...vue, onMounted: fn => mounted.push(fn), onUnmounted: fn => unmounted.push(fn) })
      const module = await sourceModule(path, path, `export { ${names.join(', ')} }`)
      await module.link(specifier => {
        if (specifier === 'vue') return componentVue
        if (specifier.endsWith('.vue')) return synthetic({ default: {} })
        if (!modules.has(specifier)) throw new Error(`Unmocked component import: ${specifier}`)
        return modules.get(specifier)
      })
      const scope = vue.effectScope()
      await scope.run(() => module.evaluate())
      return { state: module.namespace, emitted, mount: () => mounted.forEach(fn => fn()), stop() { unmounted.forEach(fn => fn()); scope.stop() } }
    },
    async writingDesk(store, props, alerts) {
      alertHandlers = alerts
      context.defineProps = () => props
      modules.set('@/composables/useJob', modules.get('useJob'))
      modules.set('@/stores/novel', synthetic({ useNovelStore: () => store }))
      modules.set('vue-router', synthetic({ useRouter: () => ({ push: path => redirects.push(path) }) }))
      modules.set('@/composables/useAlert', synthetic({ globalAlert: alerts }))
      const source = await readFile(new URL('../src/views/WritingDesk.vue', import.meta.url), 'utf8')
      const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
      const code = ts.transpileModule(`${script}\nexport { generateChapter }`, {
        compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 }
      }).outputText
      const module = new SourceTextModule(code, { context, identifier: 'WritingDesk' })
      const componentVue = synthetic({ ...vue, onMounted: () => {}, onUnmounted: () => {} })
      await module.link(specifier => {
        if (specifier === 'vue') return componentVue
        if (specifier.endsWith('.vue')) return synthetic({ default: {} })
        if (!modules.has(specifier)) throw new Error(`Unmocked WritingDesk import: ${specifier}`)
        return modules.get(specifier)
      })
      const scope = vue.effectScope()
      await scope.run(() => module.evaluate())
      return { generateChapter: module.namespace.generateChapter, stop: () => scope.stop() }
    }
  }
}
