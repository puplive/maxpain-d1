/**
 * 研究笔记用的 Markdown 工具。
 * 编辑态保存原文；查看态用 marked 解析后用 DOMPurify 过滤，防止笔记里的 HTML/脚本直接执行。
 */
import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.setOptions({ gfm: true, breaks: true })

export function renderMarkdown(source: string): string {
  const html = marked.parse(source, { async: false }) as string
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } })
}

/** 列表摘要只需要纯文字，去掉 Markdown 渲染后的标签 */
export function stripMarkdown(source: string): string {
  const div = document.createElement('div')
  div.innerHTML = renderMarkdown(source)
  return (div.textContent ?? '').replace(/\s+/g, ' ').trim()
}
