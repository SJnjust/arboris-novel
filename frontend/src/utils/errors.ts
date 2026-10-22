export function redactSensitiveText(text: string): string {
  return text.replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi, '$1[已隐藏]@')
    .replace(/\bBearer\s+(?!\[已隐藏\])[^\s"'<>;,，；}]+/gi, 'Bearer [已隐藏]')
    .replace(/\bsk-[A-Za-z0-9_-]+/g, '[已隐藏]')
    .replace(/(["']?(?:api[_-]?key|access[_-]?token|secret[_-]?key|authorization|password|passwd|token)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;，；}]+)/gi, '$1[已隐藏]')
}

export function formatApiDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail.includes('[object Object]') ? '请求失败，请稍后重试' : redactSensitiveText(detail)
  if (Array.isArray(detail)) {
    return detail.map(item => {
      if (item && typeof item === 'object' && 'msg' in item) {
        const location = Array.isArray(item.loc) ? item.loc.filter((part: unknown) => !['body', 'query', 'path'].includes(String(part))).join('.') : ''
        return redactSensitiveText(`${location ? `${location}：` : ''}${String(item.msg)}`)
      }
      return formatApiDetail(item)
    }).join('；')
  }
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const versions = 'failed_versions' in detail && Array.isArray(detail.failed_versions)
      ? detail.failed_versions.map(item => `版本 ${Number(item.index) + 1}：${formatApiDetail(item.detail)}`) : []
    return redactSensitiveText([formatApiDetail(detail.message), ...versions].join('；'))
  }
  return '请求失败，请稍后重试'
}

export function errorMessage(reason: unknown, fallback = '操作失败，请稍后重试'): string {
  const detail = reason && typeof reason === 'object' && 'detail' in reason ? reason.detail : undefined
  const message = detail !== undefined ? formatApiDetail(detail)
    : reason instanceof Error ? reason.message : typeof reason === 'string' ? reason : ''
  if (!message || message.includes('[object Object]') || !/[\u3400-\u9fff]/.test(message)) return fallback
  return redactSensitiveText(message)
}
