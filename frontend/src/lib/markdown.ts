// 轻量 Markdown 渲染（对话正文/文书预览用）
// 安全策略：html:false 禁止内嵌 HTML，输出经 Vue v-html 前已是转义文本。
import MarkdownIt from "markdown-it";

const md = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: false,
  typographer: false,
});

export function renderMarkdown(text: string | undefined | null): string {
  const raw = (text ?? "").replace(/ /g, " ");
  if (!raw.trim()) return "";
  return md.render(raw);
}
