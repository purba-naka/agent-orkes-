// ---------------------------------------------------------------------------
// Topology form — shown in the right panel when NOTHING is selected
// (doc §5.1). Edits document-level topology settings: entry node, named
// exits, recursion limit. Moved from the GraphCanvas "settings" tab; the
// entry_node_id select replaces the old per-node "Set as entry" only flow.
// ---------------------------------------------------------------------------

export interface TopologyFormProps {
  document: Record<string, any>
  onChange: (updatedDoc: Record<string, any>) => void
}

export function TopologyForm({ document, onChange }: TopologyFormProps) {
  const nodes: Array<Record<string, any>> = document.nodes || []
  const namedExits: string[] = document.named_exits || ['success']
  const recursionLimit: number = document.recursion_limit ?? 25

  function patch(patchObj: Record<string, any>) {
    onChange({ ...document, ...patchObj })
  }

  return (
    <div className="topology-form">
      <div className="config-tab-nodebar">
        <div>
          <strong>Topology</strong>
          <span className="config-tab-nodekind">click a node or edge to edit it</span>
        </div>
      </div>

      <div className="topology-fields">
      <div className="form-row">
        <label>Entry node</label>
        <select
          value={document.entry_node_id || ''}
          onChange={(e) => patch({ entry_node_id: e.target.value })}
        >
          {nodes.map((n) => (
            <option key={n.id} value={n.id}>{n.id}</option>
          ))}
        </select>
        <span className="hint">The node the run starts at.</span>
      </div>

      <div className="form-row">
        <label>Named exits</label>
        <input
          type="text"
          value={namedExits.join(', ')}
          onChange={(e) => {
            const exits = e.target.value
              .split(',')
              .map((x) => x.trim())
              .filter(Boolean)
            patch({ named_exits: exits.length > 0 ? exits : ['success'] })
          }}
        />
        <span className="hint">Comma separated. Terminal outcomes; <code>exit</code> edges and route targets can point at them.</span>
      </div>

      <div className="form-row">
        <label>Recursion limit</label>
        <input
          type="number"
          min={1}
          max={100}
          value={recursionLimit}
          onChange={(e) => {
            const val = parseInt(e.target.value, 10)
            if (!Number.isNaN(val)) patch({ recursion_limit: val })
          }}
        />
        <span className="hint">Maximum graph steps per run (1 – 100).</span>
      </div>
      </div>
    </div>
  )
}
