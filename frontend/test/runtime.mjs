import { readFile } from 'node:fs/promises'
import { createContext, SourceTextModule, SyntheticModule } from 'node:vm'
import { webcrypto } from 'node:crypto'
import { setImmediate } from 'node:timers/promises'
import ts from 'typescript'
import * as vue from 'vue'

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
  const calls = []
  const redirects = []
  const auth = { isAuthenticated: true, token: 'offline-fixture', logout() { this.isAuthenticated = false } }
  const context = createContext({ console, Error, Headers, Response, FormData, AbortController, URL, DOMException,
    crypto: webcrypto, document, localStorage: saved, setTimeout: clock.set, clearTimeout: clock.clear,
    fetch: (url, options) => { calls.push({ url, options }); return fetcher(String(url), options) } })
  const synthetic = (values) => new SyntheticModule(Object.keys(values), function () {
    for (const [name, value] of Object.entries(values)) this.setExport(name, value)
  }, { context })
  const modules = new Map([
    ['vue', synthetic(vue)],
    ['@/stores/auth', synthetic({ useAuthStore: () => auth })],
    ['@/router', synthetic({ default: { push: path => redirects.push(path) } })]
  ])
  for (const [name, path] of [['@/api/novel', '../src/api/novel.ts'], ['useJob', '../src/composables/useJob.ts']]) {
    const source = await readFile(new URL(path, import.meta.url), 'utf8')
    const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
    const module = new SourceTextModule(code, { context, identifier: name, initializeImportMeta: meta => { meta.env = { MODE: 'production' } } })
    modules.set(name, module)
    await module.link(specifier => {
      if (!modules.has(specifier)) throw new Error(`Unmocked import: ${specifier}`)
      return modules.get(specifier)
    })
    await module.evaluate()
  }
  return { api: modules.get('@/api/novel').namespace, useJob: modules.get('useJob').namespace.useJob,
    clock, calls, document, storage: saved, redirects, auth, vue,
    async writingDesk(store, props, alerts) {
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
