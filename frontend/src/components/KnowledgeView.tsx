import React, { useEffect, useState } from 'react'
import type { KnowledgeBase, KnowledgeDocument, ModelItem } from '../api'
import { api } from '../api'

const MAX_DOCUMENT_BYTES = 100_000

function modelRevisionOptions(models: ModelItem[]) {
  return models.flatMap((model) =>
    model.revisions.map((revision) => ({
      id: revision.id,
      label: `${model.name} · Rev #${revision.revision_number} · ${revision.provider}/${revision.model_name}`,
      enabled: revision.is_enabled,
    }))
  )
}

export function KnowledgeView() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([])
  const [models, setModels] = useState<ModelItem[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([])
  const [name, setName] = useState('')
  const [embeddingRevisionId, setEmbeddingRevisionId] = useState('')
  const [title, setTitle] = useState('')
  const [sourceUri, setSourceUri] = useState('')
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [documentsLoading, setDocumentsLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const revisions = modelRevisionOptions(models)
  const contentBytes = new Blob([content]).size

  async function loadCatalog() {
    try {
      setLoading(true)
      const [knowledgeBaseList, modelList] = await Promise.all([
        api.listKnowledgeBases(),
        api.listModels(),
      ])
      setKnowledgeBases(knowledgeBaseList)
      setModels(modelList)
      setSelectedId((current) => current || knowledgeBaseList[0]?.id || '')
      setEmbeddingRevisionId((current) => current || modelRevisionOptions(modelList).find((revision) => revision.enabled)?.id || '')
      setError(null)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Failed to load knowledge bases')
    } finally {
      setLoading(false)
    }
  }

  async function loadDocuments(knowledgeBaseId: string) {
    if (!knowledgeBaseId) {
      setDocuments([])
      return
    }
    try {
      setDocumentsLoading(true)
      setDocuments(await api.listKnowledgeDocuments(knowledgeBaseId))
      setError(null)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Failed to load documents')
    } finally {
      setDocumentsLoading(false)
    }
  }

  useEffect(() => {
    loadCatalog()
  }, [])

  useEffect(() => {
    loadDocuments(selectedId)
  }, [selectedId])

  async function handleCreateKnowledgeBase(event: React.FormEvent) {
    event.preventDefault()
    if (!name.trim() || !embeddingRevisionId) return
    try {
      setSubmitting(true)
      setError(null)
      const knowledgeBase = await api.createKnowledgeBase({
        name: name.trim(),
        embedding_model_revision_id: embeddingRevisionId,
      })
      setName('')
      await loadCatalog()
      setSelectedId(knowledgeBase.id)
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Failed to create knowledge base')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleIngest(event: React.FormEvent) {
    event.preventDefault()
    if (!selectedId || !title.trim() || !content.trim()) return
    if (contentBytes > MAX_DOCUMENT_BYTES) {
      setError(`Document must be at most ${MAX_DOCUMENT_BYTES.toLocaleString()} bytes`)
      return
    }
    try {
      setSubmitting(true)
      setError(null)
      await api.createKnowledgeDocument(selectedId, {
        title: title.trim(),
        content,
        source_uri: sourceUri.trim() || null,
      })
      setTitle('')
      setSourceUri('')
      setContent('')
      await loadDocuments(selectedId)
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Failed to ingest document')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleFile(file: File | undefined) {
    if (!file) return
    if (file.type && file.type !== 'text/plain') {
      setError('Only plain-text files are supported')
      return
    }
    if (file.size > MAX_DOCUMENT_BYTES) {
      setError(`Document must be at most ${MAX_DOCUMENT_BYTES.toLocaleString()} bytes`)
      return
    }
    try {
      setContent(await file.text())
      setTitle((current) => current || file.name.replace(/\.txt$/i, ''))
      setError(null)
    } catch {
      setError('Failed to read the selected file')
    }
  }

  async function handleDelete(document: KnowledgeDocument) {
    if (!selectedId || !window.confirm(`Delete “${document.title}”?`)) return
    try {
      setError(null)
      await api.deleteKnowledgeDocument(selectedId, document.id)
      await loadDocuments(selectedId)
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : 'Failed to delete document')
    }
  }

  const selectedKnowledgeBase = knowledgeBases.find((knowledgeBase) => knowledgeBase.id === selectedId)

  return (
    <div className="view-container knowledge-view">
      <div className="card">
        <div className="catalog-heading">
          <div>
            <h2>Knowledge Bases</h2>
            <p className="subtext">Create an embedding-backed collection and ingest bounded plain-text documents.</p>
          </div>
          <span className="catalog-count">{knowledgeBases.length} {knowledgeBases.length === 1 ? 'base' : 'bases'}</span>
        </div>

        {error ? <div className="alert-box error"><p>{error}</p></div> : null}

        <form onSubmit={handleCreateKnowledgeBase} className="form-grid">
          <h3>Create knowledge base</h3>
          <div className="tool-form-columns">
            <div className="form-row">
              <label htmlFor="knowledge-name">Name</label>
              <input id="knowledge-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Product documentation" required />
            </div>
            <div className="form-row">
              <label htmlFor="knowledge-model">Embedding model revision</label>
              <select id="knowledge-model" value={embeddingRevisionId} onChange={(event) => setEmbeddingRevisionId(event.target.value)} required>
                <option value="">Select a model revision</option>
                {revisions.map((revision) => <option key={revision.id} value={revision.id} disabled={!revision.enabled}>{revision.label}</option>)}
              </select>
            </div>
          </div>
          <div><button className="btn primary" disabled={submitting || !embeddingRevisionId}>Create knowledge base</button></div>
        </form>

        <div className="catalog-divider" />
        <div className="knowledge-workspace">
          <aside className="knowledge-sidebar">
            <h3>Collections</h3>
            {loading ? <p className="hint">Loading...</p> : knowledgeBases.length === 0 ? (
              <div className="empty-state"><strong>No knowledge bases</strong><span>Create one to ingest documents.</span></div>
            ) : knowledgeBases.map((knowledgeBase) => (
              <button key={knowledgeBase.id} type="button" className={`knowledge-base-button ${selectedId === knowledgeBase.id ? 'selected' : ''}`} onClick={() => setSelectedId(knowledgeBase.id)}>
                <strong>{knowledgeBase.name}</strong>
                <span>{knowledgeBase.embedding_model_revision_id}</span>
              </button>
            ))}
          </aside>

          <section className="knowledge-documents">
            <h3>{selectedKnowledgeBase?.name || 'Documents'}</h3>
            {selectedKnowledgeBase ? (
              <>
                <form onSubmit={handleIngest} className="form-grid document-form">
                  <div className="tool-form-columns">
                    <div className="form-row">
                      <label htmlFor="document-title">Title</label>
                      <input id="document-title" value={title} onChange={(event) => setTitle(event.target.value)} required />
                    </div>
                    <div className="form-row">
                      <label htmlFor="document-source">Source URI (optional)</label>
                      <input id="document-source" type="url" value={sourceUri} onChange={(event) => setSourceUri(event.target.value)} placeholder="https://example.com/docs" />
                    </div>
                  </div>
                  <div className="form-row">
                    <label htmlFor="document-file">Plain-text file (optional)</label>
                    <input id="document-file" type="file" accept="text/plain,.txt" onChange={(event) => handleFile(event.target.files?.[0])} />
                  </div>
                  <div className="form-row">
                    <label htmlFor="document-content">Document text</label>
                    <textarea id="document-content" rows={8} value={content} onChange={(event) => setContent(event.target.value)} className="prompt-area" required />
                    <span className={`hint ${contentBytes > MAX_DOCUMENT_BYTES ? 'text-danger' : ''}`}>{contentBytes.toLocaleString()} / {MAX_DOCUMENT_BYTES.toLocaleString()} bytes</span>
                  </div>
                  <div><button className="btn primary" disabled={submitting || contentBytes > MAX_DOCUMENT_BYTES}>{submitting ? 'Ingesting...' : 'Ingest document'}</button></div>
                </form>

                {documentsLoading ? <p className="hint">Loading documents...</p> : documents.length === 0 ? (
                  <div className="empty-state"><strong>No documents</strong><span>Upload or paste plain text to populate this knowledge base.</span></div>
                ) : (
                  <table className="data-table">
                    <thead><tr><th>Title</th><th>Source</th><th>Chunks</th><th>Created</th><th>Action</th></tr></thead>
                    <tbody>{documents.map((document) => (
                      <tr key={document.id}>
                        <td>{document.title}</td>
                        <td>{document.source_uri ? <a href={document.source_uri} target="_blank" rel="noreferrer">Open source</a> : '—'}</td>
                        <td>{document.chunk_count ?? '—'}</td>
                        <td>{new Date(document.created_at).toLocaleString()}</td>
                        <td><button type="button" className="btn danger small" onClick={() => handleDelete(document)}>Delete</button></td>
                      </tr>
                    ))}</tbody>
                  </table>
                )}
              </>
            ) : <div className="empty-state"><strong>Select a knowledge base</strong><span>Create or select a collection to manage documents.</span></div>}
          </section>
        </div>
      </div>
    </div>
  )
}
