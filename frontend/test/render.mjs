import { readFile } from 'node:fs/promises'
import { createContext, SourceTextModule, SyntheticModule } from 'node:vm'
import { parse, compileTemplate } from '@vue/compiler-sfc'
import * as vue from 'vue'
import ts from 'typescript'
import * as renderer from '@vue/server-renderer'

// Compile the actual template. Script behavior is covered separately by runtime.mjs.
export async function renderTemplate(path, state) {
  const filename = new URL(path, import.meta.url)
  const { descriptor } = parse(await readFile(filename, 'utf8'))
  const compiled = compileTemplate({ source: descriptor.template.content, filename: filename.pathname, id: 'fix2', ssr: true, cssVars: [] })
  if (compiled.errors.length) throw new Error(compiled.errors.join('\n'))
  const context = createContext({ console })
  const synthetic = values => new SyntheticModule(Object.keys(values), function () {
    for (const [name, value] of Object.entries(values)) this.setExport(name, value)
  }, { context })
  const module = new SourceTextModule(ts.transpileModule(compiled.code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText, { context })
  await module.link(name => synthetic(name === 'vue' ? vue : renderer))
  await module.evaluate()
  const stub = { setup: (_props, { slots }) => () => vue.h('span', slots.default?.()) }
  const component = vue.defineComponent({ ssrRender: module.namespace.ssrRender, setup: () => ({ props: state, ...state }),
    components: { Tooltip: stub, BlueprintEditModal: stub } })
  return renderer.renderToString(vue.createSSRApp(component))
}
