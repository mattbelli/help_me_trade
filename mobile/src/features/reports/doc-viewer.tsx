import { useMemo, useRef, type ReactElement } from 'react';
import { WebView } from 'react-native-webview';
import { marked } from 'marked';

import { colors } from '@/theme';

marked.setOptions({ gfm: true });

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function buildBody(content: string, mode: 'text' | 'html' | 'markdown'): string {
  if (mode === 'html') return content;
  if (mode === 'text') return `<pre class="report-pre">${escapeHtml(content)}</pre>`;
  return marked.parse(content, { async: false });
}

function buildHtml(body: string): string {
  const { bg, text, textMuted, textFaint, border, surfaceRaised, accent } = colors;
  return `<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
html,body { margin:0; background:${bg}; }
.md { font-family: ui-monospace, Menlo, monospace; font-size:12px; line-height:1.65;
      color:${textMuted}; max-width:65ch; margin:0 auto; padding:16px 18px 48px; }
.md h1 { margin:0 0 16px; font-size:18px; font-weight:600; color:${text}; }
.md h2 { margin:24px 0 12px; font-size:16px; font-weight:600; color:${text}; }
.md h3 { margin:16px 0 8px; font-size:14px; font-weight:600; color:${text}; }
.md p { margin:12px 0; } .md p:first-child{margin-top:0;} .md p:last-child{margin-bottom:0;}
.md ul { margin:12px 0; padding-left:20px; list-style:disc; }
.md ol { margin:12px 0; padding-left:20px; list-style:decimal; }
.md li { line-height:1.65; margin:4px 0; }
.md strong { font-weight:600; color:${text}; } .md em { font-style:italic; }
.md blockquote { margin:12px 0; border-left:2px solid ${border}; padding-left:12px; color:${textFaint}; }
.md hr { margin:24px 0; border:0; border-top:1px solid ${border}; }
.md a { color:${accent}; text-decoration:underline; }
.md table { margin:16px 0; width:100%; border-collapse:collapse; font-size:11px; }
.md th { border:1px solid ${border}; background:${surfaceRaised}; padding:6px 8px; text-align:left; font-weight:500; color:${text}; }
.md td { border:1px solid ${border}; padding:6px 8px; vertical-align:top; }
.md code { font-family:ui-monospace,Menlo,monospace; }
.report-pre { font-family:ui-monospace,Menlo,monospace; font-size:12px; line-height:1.65;
              white-space:pre-wrap; color:${textMuted}; margin:0; }
</style>
</head>
<body><div class="md">${body}</div></body>
</html>`;
}

export function DocViewer({
  content,
  mode,
}: {
  content: string;
  mode: 'text' | 'html' | 'markdown';
}): ReactElement {
  const initialLoad = useRef(true);
  const html = useMemo(() => buildHtml(buildBody(content, mode)), [content, mode]);

  return (
    <WebView
      source={{ html }}
      originWhitelist={['*']}
      style={{ flex: 1, backgroundColor: 'transparent' }}
      scrollEnabled
      showsVerticalScrollIndicator
      onShouldStartLoadWithRequest={(req) => {
        if (initialLoad.current) {
          initialLoad.current = false;
          return true;
        }
        if (req.url.startsWith('http://') || req.url.startsWith('https://')) {
          return false;
        }
        return true;
      }}
    />
  );
}
