import React, { useEffect, useState } from 'react'
import type { Credential } from '../api'
import { api } from '../api'

export function CredentialsView() {
  const [credentials, setCredentials] = useState<Credential[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Form state for creating credential
  const [name, setName] = useState('')
  const [kind, setKind] = useState('api_key')
  const [secret, setSecret] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // State for updating secret
  const [editingId, setEditingId] = useState<string | null>(null)
  const [newSecret, setNewSecret] = useState('')

  async function loadCredentials() {
    try {
      setLoading(true)
      const list = await api.listCredentials()
      setCredentials(list)
      setError(null)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadCredentials()
  }, [])

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    if (!name || !secret) return
    try {
      setSubmitting(true)
      await api.createCredential({ name, kind, secret })
      setName('')
      setSecret('')
      await loadCredentials()
    } catch (err: any) {
      alert(`Error: ${err.message}`)
    } finally {
      setSubmitting(false)
    }
  }

  async function handleUpdateSecret(id: string) {
    if (!newSecret) return
    try {
      await api.updateCredential(id, newSecret)
      setEditingId(null)
      setNewSecret('')
      await loadCredentials()
    } catch (err: any) {
      alert(`Error: ${err.message}`)
    }
  }

  async function handleDisable(id: string) {
    if (!confirm('Disable this credential? It will no longer be usable by model revisions.')) return
    try {
      await api.disableCredential(id)
      await loadCredentials()
    } catch (err: any) {
      alert(`Error: ${err.message}`)
    }
  }

  return (
    <div className="view-container">
      <div className="card">
        <h2>Credentials Vault</h2>
        <p className="subtext">
          Encrypted AES-256-GCM credentials. Plaintext secrets are never returned in responses or logs.
        </p>

        {error && <div className="alert-box error"><p>{error}</p></div>}

        <form onSubmit={handleCreate} className="form-grid">
          <h3>Add New Credential</h3>
          <div className="form-row">
            <label>Name</label>
            <input
              type="text"
              placeholder="e.g. openai-prod-key"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="form-row">
            <label>Kind</label>
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="api_key">API Key</option>
              <option value="oauth_token">OAuth Token</option>
              <option value="custom">Custom</option>
            </select>
          </div>
          <div className="form-row">
            <label>Secret</label>
            <input
              type="password"
              placeholder="sk-..."
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              required
            />
          </div>
          <button type="submit" disabled={submitting} className="btn primary">
            {submitting ? 'Saving...' : 'Add Credential'}
          </button>
        </form>

        <hr className="divider" />

        <h3>Configured Credentials</h3>
        {loading ? (
          <p>Loading credentials...</p>
        ) : credentials.length === 0 ? (
          <p className="hint">No credentials added yet.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Kind</th>
                <th>Secret</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {credentials.map((c) => (
                <tr key={c.id}>
                  <td className="font-semibold">{c.name}</td>
                  <td><span className="badge">{c.kind}</span></td>
                  <td>
                    <code>••••••••{c.last_four}</code>
                  </td>
                  <td>
                    <span className={`status-badge ${c.is_enabled ? 'active' : 'disabled'}`}>
                      {c.is_enabled ? 'Active' : 'Disabled'}
                    </span>
                  </td>
                  <td>
                    <div className="action-buttons">
                      {editingId === c.id ? (
                        <div className="inline-edit">
                          <input
                            type="password"
                            placeholder="New secret"
                            value={newSecret}
                            onChange={(e) => setNewSecret(e.target.value)}
                          />
                          <button onClick={() => handleUpdateSecret(c.id)} className="btn small primary">Save</button>
                          <button onClick={() => setEditingId(null)} className="btn small">Cancel</button>
                        </div>
                      ) : (
                        <>
                          <button onClick={() => setEditingId(c.id)} className="btn small">
                            Rotate
                          </button>
                          {c.is_enabled && (
                            <button onClick={() => handleDisable(c.id)} className="btn small danger">
                              Disable
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
