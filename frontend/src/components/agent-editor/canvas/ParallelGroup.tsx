// ---------------------------------------------------------------------------
// Parallel group overlay — dashed bounding box behind nodes.
//
// Rendered inside <ViewportPortal> so it pans and zooms with the canvas;
// rendering it outside makes it sit still while nodes move, which looks
// clearly wrong. z-index stays below the nodes.
// ---------------------------------------------------------------------------

import type { ParallelGroup } from '../lib/layout'

export function ParallelGroupBox({ group }: { group: ParallelGroup }) {
  return (
    <div
      className="parallel-group-box"
      style={{
        position: 'absolute',
        left: group.x,
        top: group.y,
        width: group.width,
        height: group.height,
      }}
      aria-hidden="true"
    >
      <span className="parallel-group-label">{group.label}</span>
    </div>
  )
}
