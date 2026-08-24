/** Local pin board for claims — reusable across runs without API schema change. */

export type PinnedClaim = {
  id: string;
  text: string;
  url?: string;
  quote?: string;
  confidence?: number;
  runId?: string;
  query?: string;
  n?: number;
  pinnedAt: string;
};

const KEY = "kiln_pinned_claims";

export function loadPinnedClaims(): PinnedClaim[] {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function savePinnedClaims(items: PinnedClaim[]) {
  localStorage.setItem(KEY, JSON.stringify(items.slice(0, 200)));
}

export function pinClaim(claim: Omit<PinnedClaim, "id" | "pinnedAt"> & { id?: string }) {
  const items = loadPinnedClaims();
  const id =
    claim.id ||
    `pin_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  if (items.some((x) => x.text === claim.text && x.runId === claim.runId)) {
    return items;
  }
  const next: PinnedClaim[] = [
    { ...claim, id, pinnedAt: new Date().toISOString() },
    ...items,
  ];
  savePinnedClaims(next);
  return next;
}

export function unpinClaim(id: string) {
  const next = loadPinnedClaims().filter((x) => x.id !== id);
  savePinnedClaims(next);
  return next;
}
