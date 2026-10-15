export function resolveCitations<T extends { id: string }>(evidence: T[], ids: string[]) {
  const byId = new Map<string, T[]>();
  for (const item of evidence) byId.set(item.id, [...(byId.get(item.id) ?? []), item]);
  const requested = [...new Set(ids)];
  const cited: T[] = [];
  const missing: string[] = [];
  const ambiguous: string[] = [];
  for (const id of requested) {
    const matches = byId.get(id) ?? [];
    if (matches.length === 0) missing.push(id);
    else if (matches.length !== 1) ambiguous.push(id);
    else cited.push(matches[0]);
  }
  return { cited, missing, ambiguous };
}

export function sourceLink(source: string): string | null {
  try {
    const url = new URL(source);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

export function evidenceAnchor(id: string): string {
  return `evidence-${encodeURIComponent(id)}`;
}
