import type { FeedNotification } from '@/api/types';

const SYMBOL_KINDS = new Set([
  'fill_report',
  'price_alert',
  'earnings',
  'risk_breaker',
  'risk_block',
]);

function pathOnly(actionUrl: string): string {
  return actionUrl.split('?')[0] ?? actionUrl;
}

function lastPathSegment(path: string): string | null {
  const segments = pathOnly(path).split('/').filter(Boolean);
  return segments.length > 0 ? (segments[segments.length - 1] ?? null) : null;
}

function queryParam(actionUrl: string, key: string): string | null {
  const queryPart = actionUrl.split('?')[1];
  if (!queryPart) return null;
  for (const pair of queryPart.split('&')) {
    const [k, v] = pair.split('=');
    if (k === key && v) return decodeURIComponent(v);
  }
  return null;
}

function idFromData(
  data: Record<string, unknown> | undefined,
  ...keys: string[]
): string | null {
  if (!data) return null;
  for (const key of keys) {
    const val = data[key];
    if (typeof val === 'string' && val.trim()) return val.trim();
    if (typeof val === 'number' && Number.isFinite(val)) return String(val);
  }
  return null;
}

function symbolFromPath(actionUrl: string): string | null {
  const match = pathOnly(actionUrl).match(/^\/(?:symbol|watchlist)\/([^/]+)/);
  return match?.[1] ? match[1].toUpperCase() : null;
}

function resolveSymbol(n: FeedNotification): string | null {
  const fromData =
    typeof n.data?.symbol === 'string' ? n.data.symbol.trim().toUpperCase() : '';
  if (fromData) return fromData;
  const actionUrl = n.action_url ?? '';
  return symbolFromPath(actionUrl);
}

export function notificationRoute(n: FeedNotification): string | null {
  const actionUrl = n.action_url ?? '';

  if (n.kind === 'report_ready' || actionUrl.startsWith('/reports/')) {
    const id =
      idFromData(n.data, 'report_id') ??
      (n.ref_id?.trim() || null) ??
      (actionUrl.startsWith('/reports/') ? lastPathSegment(actionUrl) : null);
    return id ? `/report/${id}` : null;
  }

  if (n.kind === 'research_ready' || actionUrl.startsWith('/research')) {
    const id =
      idFromData(n.data, 'research_id', 'artifact_id') ??
      (n.ref_id?.trim() || null) ??
      queryParam(actionUrl, 'id') ??
      (pathOnly(actionUrl).startsWith('/research/') ? lastPathSegment(actionUrl) : null);
    return id ? `/research/${id}` : null;
  }

  const sym = resolveSymbol(n);
  const hasDataSymbol = typeof n.data?.symbol === 'string';
  const hasSymbolPath = /^\/(?:symbol|watchlist)\//.test(pathOnly(actionUrl));
  if (sym && (SYMBOL_KINDS.has(n.kind) || hasDataSymbol || hasSymbolPath)) {
    return `/symbol/${sym}`;
  }

  if (pathOnly(actionUrl) === '/alerts') {
    return '/alerts';
  }

  return null;
}
