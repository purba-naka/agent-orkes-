import React, { useEffect, useState } from 'react'
import type { Credential, ModelItem, ModelTestResult } from '../api'
import { api } from '../api'

export function ModelsView() {
  const [models, setModels] = useState<ModelItem[]>([])
  const [credentials, setCredentials] = useState<Credential[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Form state for creating model
  const [name, setName] = useState('')
  const [provider, setProvider] = useState('openai')
  const [modelName, setModelName] = useState('gpt-4o-mini')
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKeyId, setApiKeyId] = useState('')
  const [temperature, setTemperature] = useState('0.7')
  const [submitting, setSubmitting] = useState(false)

  // Revision creation modal / inline state
  const [selectedModel, setSelectedModel] = useState<ModelItem | null>(null)
  const [revProvider, setRevProvider] = useState('openai')
  const [revModelName, setRevModelName] = useState('')
  const [revBaseUrl, setRevBaseUrl] = useState('')
  const [revApiKeyId, setRevApiKeyId] = useState('')
  const [revTemp, setRevTemp] = useState('0.7')
  const [revSubmitting, setRevSubmitting] = useState(false)

  // Connection test state (per model)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, ModelTestResult>>({})

  async function loadData() {
    try {
      setLoading(true)
      const [mList, cList] = await Promise.all([
        api.listModels(),
        api.listCredentials(),
      ])
      setModels(mList)
      setCredentials(cList.filter((c) => c.is_enabled))
      if (cList.length > 0 && !apiKeyId) {
        setApiKeyId(cList[0].id)
      }
      setError(null)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  async function handleCreateModel(e: React.FormEvent) {
    e.preventDefault()
    if (!name || !provider || !modelName) return

    try {
      setSubmitting(true)
      await api.createModel({
        name,
        revision: {
          provider,
          model_name: modelName,
          base_url: baseUrl.trim() ? baseUrl.trim() : null,
          api_key_id: apiKeyId ? apiKeyId : null,
          parameters: {
            temperature: parseFloat(temperature) || 0,
          },
        },
      })
      setName('')
      setBaseUrl('')
      await loadData()
    } catch (err: any) {
      alert(`Error: ${err.message}`)
    } finally {
      setSubmitting(false)
    }
  }

  async function handleCreateRevision(e: React.FormEvent) {
    e.preventDefault()
    if (!selectedModel || !revProvider || !revModelName) return

    try {
      setRevSubmitting(true)
      await api.createModelRevision(selectedModel.id, {
        provider: revProvider,
        model_name: revModelName,
        base_url: revBaseUrl.trim() ? revBaseUrl.trim() : null,
        api_key_id: revApiKeyId ? revApiKeyId : null,
        parameters: {
          temperature: parseFloat(revTemp) || 0,
        },
      })
      setSelectedModel(null)
      await loadData()
    } catch (err: any) {
      alert(`Error: ${err.message}`)
    } finally {
      setRevSubmitting(false)
    }
  }

  async function handleTestModel(model: ModelItem) {
    if (testingId) return
    setTestingId(model.id)
    // Clear previous result while re-testing
    setTestResults((prev) => {
      const next = { ...prev }
      delete next[model.id]
      return next
    })
    try {
      const result = await api.testModel(model.id)
      setTestResults((prev) => ({ ...prev, [model.id]: result }))
    } catch (err: any) {
      setTestResults((prev) => ({
        ...prev,
        [model.id]: {
          status: 'failed',
          model_id: model.id,
          revision_id: null,
          revision_number: null,
          latency_ms: null,
          response_preview: null,
          error: err.message,
        },
      }))
    } finally {
      setTestingId(null)
    }
  }

  function openRevisionModal(model: ModelItem) {
    setSelectedModel(model)
    if (model.active_revision) {
      setRevProvider(model.active_revision.provider)
      setRevModelName(model.active_revision.model_name)
      setRevBaseUrl(model.active_revision.base_url || '')
      setRevApiKeyId(model.active_revision.api_key_id || '')
      setRevTemp(String(model.active_revision.parameters?.temperature ?? 0.7))
    } else {
      setRevProvider('openai')
      setRevModelName('')
      setRevBaseUrl('')
      setRevApiKeyId(credentials[0]?.id || '')
      setRevTemp('0.7')
    }
  }

  return (
    <div className="view-container">
      <div className="card">
        <h2>Models & Revisions Catalog</h2>
        <p className="subtext">
          Model configurations resolved via in-process LiteLLM. Updating a model creates an immutable revision.
        </p>

        {error && <div className="alert-box error"><p>{error}</p></div>}

        <form onSubmit={handleCreateModel} className="form-grid">
          <h3>Register New Model</h3>
          <div className="form-row">
            <label>Model Identity Name</label>
            <input
              type="text"
              placeholder="e.g. primary-gpt-4o"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="form-row">
            <label>Provider</label>
            <select value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
              <option value="groq">Groq</option>
              <option value="ollama">Ollama / Local</option>
              <option value="fake">Fake / Test Model</option>
            </select>
          </div>
          <div className="form-row">
            <label>Model Identifier</label>
            <input
              type="text"
              placeholder="e.g. gpt-4o-mini or claude-3-5-sonnet"
              value={modelName}
              onChange={(e) => setModelName(e.target.value)}
              required
            />
          </div>
          <div className="form-row">
            <label>API Key / Credential</label>
            <select value={apiKeyId} onChange={(e) => setApiKeyId(e.target.value)}>
              <option value="">-- None / Local / Test --</option>
              {credentials.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} (••••{c.last_four})
                </option>
              ))}
            </select>
          </div>
          <div className="form-row">
            <label>Base URL (optional)</label>
            <input
              type="text"
              placeholder="e.g. https://api.openai.com/v1"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </div>
          <div className="form-row">
            <label>Temperature</label>
            <input
              type="number"
              step="0.1"
              min="0"
              max="2"
              value={temperature}
              onChange={(e) => setTemperature(e.target.value)}
            />
          </div>
          <button type="submit" disabled={submitting} className="btn primary">
            {submitting ? 'Creating...' : 'Register Model'}
          </button>
        </form>

        <hr className="divider" />

        <h3>Registered Models</h3>
        {loading ? (
          <p>Loading models...</p>
        ) : models.length === 0 ? (
          <p className="hint">No models configured yet.</p>
        ) : (
          <div className="models-list">
            {models.map((m) => {
              const active = m.active_revision
              return (
                <div key={m.id} className="model-card">
                  <div className="model-header">
                    <div>
                      <h4>{m.name}</h4>
                      <span className="badge">Active Rev #{active?.revision_number || 1}</span>
                    </div>
                    <div style={{ display: 'flex', gap: '0.5rem' }}>
                      <button
                        onClick={() => openRevisionModal(m)}
                        className="btn small primary"
                      >
                        + New Revision
                      </button>
                      <button
                        onClick={() => handleTestModel(m)}
                        className="btn small"
                        disabled={testingId === m.id || !active}
                        title={active ? 'Send a ping to verify the model connection' : 'No active revision'}
                      >
                        {testingId === m.id ? 'Testing...' : 'Test Connection'}
                      </button>
                    </div>
                  </div>
                  {active && (
                    <div className="revision-details">
                      <div className="detail-item">
                        <span className="label">Target:</span>
                        <code>{active.provider}/{active.model_name}</code>
                      </div>
                      {active.base_url && (
                        <div className="detail-item">
                          <span className="label">Base URL:</span>
                          <span>{active.base_url}</span>
                        </div>
                      )}
                      <div className="detail-item">
                        <span className="label">Temperature:</span>
                        <span>{active.parameters?.temperature ?? 0}</span>
                      </div>
                      <div className="detail-item">
                        <span className="label">Total Revisions:</span>
                        <span>{m.revisions?.length || 1}</span>
                      </div>
                    </div>
                  )}
                  {testResults[m.id] && (
                    <div className={`alert-box ${testResults[m.id].status === 'connected' ? 'success' : 'error'}`}>
                      {testResults[m.id].status === 'connected' ? (
                        <p>
                          ✅ Connected in {testResults[m.id].latency_ms} ms
                          {testResults[m.id].response_preview && (
                            <> — reply: <code>{testResults[m.id].response_preview}</code></>
                          )}
                        </p>
                      ) : (
                        <p>❌ {testResults[m.id].error || 'Connection failed'}</p>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {/* Modal / Dialog for New Revision */}
        {selectedModel && (
          <div className="modal-overlay">
            <div className="modal-card">
              <h3>Create Revision for "{selectedModel.name}"</h3>
              <p className="subtext">
                This will increment the revision number and become the new active revision.
              </p>
              <form onSubmit={handleCreateRevision} className="form-grid">
                <div className="form-row">
                  <label>Provider</label>
                  <select value={revProvider} onChange={(e) => setRevProvider(e.target.value)}>
                    <option value="openai">OpenAI</option>
                    <option value="anthropic">Anthropic</option>
                    <option value="groq">Groq</option>
                    <option value="ollama">Ollama / Local</option>
                    <option value="fake">Fake / Test Model</option>
                  </select>
                </div>
                <div className="form-row">
                  <label>Model Identifier</label>
                  <input
                    type="text"
                    value={revModelName}
                    onChange={(e) => setRevModelName(e.target.value)}
                    required
                  />
                </div>
                <div className="form-row">
                  <label>API Key / Credential</label>
                  <select value={revApiKeyId} onChange={(e) => setRevApiKeyId(e.target.value)}>
                    <option value="">-- None / Local / Test --</option>
                    {credentials.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name} (••••{c.last_four})
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-row">
                  <label>Base URL (optional)</label>
                  <input
                    type="text"
                    value={revBaseUrl}
                    onChange={(e) => setRevBaseUrl(e.target.value)}
                  />
                </div>
                <div className="form-row">
                  <label>Temperature</label>
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    max="2"
                    value={revTemp}
                    onChange={(e) => setRevTemp(e.target.value)}
                  />
                </div>
                <div className="modal-actions">
                  <button type="submit" disabled={revSubmitting} className="btn primary">
                    {revSubmitting ? 'Saving...' : 'Save Revision'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setSelectedModel(null)}
                    className="btn"
                  >
                    Cancel
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
