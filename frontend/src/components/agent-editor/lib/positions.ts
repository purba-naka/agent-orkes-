// ---------------------------------------------------------------------------
// Position cache — localStorage persistence for manual node layouts.
//
// Adding a node keeps every existing position. A full relayout was correct
// but threw away the layout the user had arranged by hand and made the whole
// canvas visibly jump on every insert. The caller places whatever is missing
// via placeNewNodes(), which stacks new nodes clear of the existing bounding
// box so nothing already on screen moves.
//
// The stored signature is kept for diagnostics only; it no longer gates the
// read.
//
// IMPORTANT: node positions never live in the agent document itself. The
// canonical content_hash would otherwise produce a new revision for every
// pixel of drag with identical runtime behavior.
// ---------------------------------------------------------------------------

interface StoredLayout {
  /** sorted, comma-joined node IDs — used for change detection */
  signature: string
  positions: Record<string, { x: number; y: number }>
}

const KEY_PREFIX = 'agent-layout:'

function storageKey(agentId: string): string {
  return `${KEY_PREFIX}${agentId}`
}

export function computeSignature(nodeIds: string[]): string {
  return [...nodeIds].sort().join(',')
}

/**
 * Returns the cached positions for the nodes that have one, or null when
 * there is nothing usable (no entry, or corrupted JSON — the caller does not
 * need to know which).
 *
 * The result may be PARTIAL: nodes added since the layout was saved are
 * absent, and stale entries for deleted nodes are dropped. Callers must place
 * the remainder themselves.
 */
export function loadPositions(
  agentId: string,
  nodeIds: string[]
): Record<string, { x: number; y: number }> | null {
  try {
    const raw = localStorage.getItem(storageKey(agentId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as StoredLayout
    if (!parsed || typeof parsed !== 'object' || !parsed.positions) return null
    const wanted: Record<string, { x: number; y: number }> = {}
    nodeIds.forEach((id) => {
      const pos = parsed.positions[id]
      if (pos && typeof pos.x === 'number' && typeof pos.y === 'number') wanted[id] = pos
    })
    return Object.keys(wanted).length > 0 ? wanted : null
  } catch {
    // Corrupted JSON (or a browser that throws on read): fall back to a
    // fresh layout. A broken cache must never take the editor down.
    return null
  }
}

/**
 * Persists positions. Storage failures (private browsing, quota) are
 * swallowed on purpose — the user only loses their manual layout, not
 * their work.
 */
export function savePositions(
  agentId: string,
  nodeIds: string[],
  positions: Record<string, { x: number; y: number }>
): void {
  try {
    const stored: StoredLayout = {
      signature: computeSignature(nodeIds),
      positions,
    }
    localStorage.setItem(storageKey(agentId), JSON.stringify(stored))
  } catch {
    // Private browsing / quota exceeded: silently ignore.
  }
}

/** Drops the cached layout so the next render runs `runLayout` again. */
export function clearPositions(agentId: string): void {
  try {
    localStorage.removeItem(storageKey(agentId))
  } catch {
    // Ignore — nothing to clean up if storage is unavailable.
  }
}
