/**
 * Defense-in-depth: strip any tool-call markup the model may leak into its text.
 *
 * The mobile chat brain's final synthesis call can emit raw `<function_calls>`
 * XML as text (a serv-side prompt bug — the final complete() is handed the full
 * agentic system prompt, so the model "calls" tools the only way plain text
 * allows). A chat client should never render that markup as a message, so we
 * scrub it at the last moment before display. Once serv gives the synthesis call
 * a tools-free prompt this becomes harmless dead weight — keep it as a guard.
 */
export function stripToolMarkup(text: string): string {
  if (!text.includes('<')) return text;
  return text
    // Complete tool-call blocks.
    .replace(/<function_calls>[\s\S]*?<\/function_calls>/g, '')
    // An unclosed block still streaming in — drop from the tag onward so partial
    // XML never flashes token-by-token.
    .replace(/<function_calls>[\s\S]*$/g, '')
    // Stray tool tags that leaked without the wrapper.
    .replace(/<\/?(?:invoke|parameter|function_calls)\b[^>]*>/g, '')
    .trimEnd();
}
