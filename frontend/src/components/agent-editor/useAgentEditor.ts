// ---------------------------------------------------------------------------
// Agent editor reducer — single source of truth for editor state.
//
// The old AgentsView had a dozen separate useState hooks; its actions are
// interrelated (selecting a node must clear edge selection; save must set
// version + diagnostics + dirty together) and forgetting one is how bugs
// are born. One reducer keeps them atomic.
// ---------------------------------------------------------------------------

import { useCallback, useMemo, useReducer } from 'react'
import type { Diagnostic } from '../../api'
import { api, DraftConflictError, PublishValidationError } from '../../api'
import type { AgentItem, ModelItem, ToolItem } from '../../api'

export interface EditorState {
  doc: Record<string, any> | null
  /** serialized copy of the document right after the last save — dirty tracking */
  savedDoc: string | null
  version: number
  diagnostics: Diagnostic[]
  selection:
    | { type: 'none' }
    | { type: 'node'; nodeId: string }
    | { type: 'edge'; edgeIndex: number }
  saving: boolean
  publishing: boolean
  conflict: { currentVersion: number; currentDraft: Record<string, any> } | null
  status: string | null
  /** serial for toasts so repeated identical messages still re-appear */
  statusAt: number
}

export type EditorAction =
  | { type: 'loaded'; doc: Record<string, any>; version: number; diagnostics: Diagnostic[] }
  | { type: 'docChanged'; doc: Record<string, any> }
  | { type: 'selectNode'; nodeId: string }
  | { type: 'selectEdge'; edgeIndex: number }
  | { type: 'clearSelection' }
  | { type: 'saveStart' }
  | { type: 'saveSuccess'; doc: Record<string, any>; version: number; diagnostics: Diagnostic[] }
  | { type: 'saveConflict'; currentVersion: number; currentDraft: Record<string, any> }
  | { type: 'saveError'; message: string }
  | { type: 'dismissConflict' }
  | { type: 'publishStart' }
  | { type: 'publishSuccess'; revisionNumber: number; hash: string }
  | { type: 'publishInvalid'; diagnostics: Diagnostic[] }
  | { type: 'publishError'; message: string }
  | { type: 'setStatus'; message: string | null }

export const initialEditorState: EditorState = {
  doc: null,
  savedDoc: null,
  version: 1,
  diagnostics: [],
  selection: { type: 'none' },
  saving: false,
  publishing: false,
  conflict: null,
  status: null,
  statusAt: 0,
}

/** Lazy reducer initializer — seeds the editor from the agent's draft. */
function initFromAgent(agent: AgentItem): EditorState {
  if (agent.draft) {
    return {
      ...initialEditorState,
      doc: agent.draft.document,
      savedDoc: JSON.stringify(agent.draft.document),
      version: agent.draft.version,
      diagnostics: agent.draft.validation || [],
    }
  }
  return initialEditorState
}

export function editorReducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case 'loaded':
      return {
        ...state,
        doc: action.doc,
        savedDoc: JSON.stringify(action.doc),
        version: action.version,
        diagnostics: action.diagnostics,
        selection: { type: 'none' },
        saving: false,
        publishing: false,
        conflict: null,
      }
    case 'docChanged':
      return { ...state, doc: action.doc }
    case 'selectNode':
      return { ...state, selection: { type: 'node', nodeId: action.nodeId } }
    case 'selectEdge':
      return { ...state, selection: { type: 'edge', edgeIndex: action.edgeIndex } }
    case 'clearSelection':
      return { ...state, selection: { type: 'none' } }
    case 'saveStart':
      return { ...state, saving: true, status: 'Saving draft…', statusAt: Date.now() }
    case 'saveSuccess':
      return {
        ...state,
        doc: action.doc,
        savedDoc: JSON.stringify(action.doc),
        version: action.version,
        diagnostics: action.diagnostics,
        saving: false,
        conflict: null,
        status: `Draft v${action.version} saved.`,
        statusAt: Date.now(),
      }
    case 'saveConflict':
      return {
        ...state,
        saving: false,
        conflict: { currentVersion: action.currentVersion, currentDraft: action.currentDraft },
        status: null,
      }
    case 'saveError':
      return { ...state, saving: false, status: `Error saving: ${action.message}`, statusAt: Date.now() }
    case 'dismissConflict':
      return { ...state, conflict: null }
    case 'publishStart':
      return { ...state, publishing: true, status: 'Publishing…', statusAt: Date.now() }
    case 'publishSuccess':
      return {
        ...state,
        publishing: false,
        status: `Published revision #${action.revisionNumber} (${action.hash.slice(0, 10)}…)`,
        statusAt: Date.now(),
      }
    case 'publishInvalid':
      return {
        ...state,
        publishing: false,
        diagnostics: action.diagnostics,
        status: `Publish failed with ${action.diagnostics.length} diagnostic(s).`,
        statusAt: Date.now(),
      }
    case 'publishError':
      return { ...state, publishing: false, status: `Publish error: ${action.message}`, statusAt: Date.now() }
    case 'setStatus':
      return { ...state, status: action.message, statusAt: Date.now() }
    default:
      return state
  }
}

// ---------------------------------------------------------------------------
// Hook wrapper — owns the reducer plus the save/publish callbacks.
// ---------------------------------------------------------------------------

export interface UseAgentEditorArgs {
  agent: AgentItem
  models: ModelItem[]
  tools: ToolItem[]
  agents: AgentItem[]
  onExit: () => void
  onAgentChanged: () => void
}

export function useAgentEditor({ agent, onAgentChanged }: UseAgentEditorArgs) {
  // EditorShell is rendered with key={agent.id}, so per-agent initialization
  // through the lazy reducer initializer is correct.
  const [state, dispatch] = useReducer(editorReducer, agent, initFromAgent)

  const saveDraft = useCallback(
    async (overrideVersion?: number) => {
      if (state.doc == null) return
      dispatch({ type: 'saveStart' })
      try {
        const updated = await api.updateAgentDraft(
          agent.id,
          overrideVersion ?? state.version,
          state.doc
        )
        dispatch({
          type: 'saveSuccess',
          doc: updated.document,
          version: updated.version,
          diagnostics: updated.validation || [],
        })
      } catch (err) {
        if (err instanceof DraftConflictError) {
          dispatch({
            type: 'saveConflict',
            currentVersion: err.currentVersion,
            currentDraft: err.currentDraft,
          })
        } else if (err instanceof Error) {
          dispatch({ type: 'saveError', message: err.message })
        }
      }
    },
    [agent.id, state.doc, state.version]
  )

  const publish = useCallback(async () => {
    dispatch({ type: 'publishStart' })
    try {
      const rev = await api.publishAgent(agent.id)
      dispatch({
        type: 'publishSuccess',
        revisionNumber: rev.revision_number,
        hash: rev.content_hash,
      })
      onAgentChanged()
    } catch (err) {
      if (err instanceof PublishValidationError) {
        dispatch({ type: 'publishInvalid', diagnostics: err.diagnostics })
      } else if (err instanceof Error) {
        dispatch({ type: 'publishError', message: err.message })
      }
    }
  }, [agent.id, onAgentChanged])

  /** 409 recovery: discard my changes, take the server's draft. */
  const reloadFromServer = useCallback(() => {
    if (!state.conflict) return
    dispatch({
      type: 'loaded',
      doc: state.conflict.currentDraft,
      version: state.conflict.currentVersion,
      diagnostics: [],
    })
    dispatch({ type: 'setStatus', message: 'Reloaded the newer server draft.' })
  }, [state.conflict])

  /** 409 recovery: save again with the server's current version. */
  const overwriteServer = useCallback(() => {
    if (!state.conflict) return
    const version = state.conflict.currentVersion
    dispatch({ type: 'dismissConflict' })
    void saveDraft(version)
  }, [state.conflict, saveDraft])

  const isDirty = useMemo(
    () =>
      state.savedDoc !== null &&
      state.doc !== null &&
      JSON.stringify(state.doc) !== state.savedDoc,
    [state.doc, state.savedDoc]
  )

  return {
    state,
    dispatch,
    isDirty,
    saveDraft,
    publish,
    reloadFromServer,
    overwriteServer,
  }
}
