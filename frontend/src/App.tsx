import { useEffect, useState } from 'react'
import {
  Bot,
  Cpu,
  KeyRound,
  LayoutDashboard,
  Library,
  MessageSquare,
  Moon,
  PanelLeft,
  Plus,
  Sun,
  Wrench,
  Menu,
  X,
  type LucideIcon,
} from 'lucide-react'
import './App.css'
import type { ConversationItem } from './api'
import { api } from './api'
import { AgentsView } from './components/AgentsView'
import { ConversationsView } from './components/ConversationsView'
import { CredentialsView } from './components/CredentialsView'
import { ModelsView } from './components/ModelsView'
import { KnowledgeView } from './components/KnowledgeView'
import { ToolsView } from './components/ToolsView'

interface HealthState {
  health: { status: string } | null
  ready: { status: string; database: string } | null
  error: string | null
  loading: boolean
}

type Tab = 'conversations' | 'agents' | 'tools' | 'knowledge' | 'models' | 'credentials' | 'overview'

interface NavItem {
  id: Tab
  label: string
  icon: LucideIcon
}

const PRIMARY_NAV: NavItem[] = [
  { id: 'conversations', label: 'Chat', icon: MessageSquare },
  { id: 'agents', label: 'Agents', icon: Bot },
  { id: 'tools', label: 'Tools', icon: Wrench },
  { id: 'knowledge', label: 'Knowledge', icon: Library },
]

const SYSTEM_NAV: NavItem[] = [
  { id: 'models', label: 'Models', icon: Cpu },
  { id: 'credentials', label: 'Credentials', icon: KeyRound },
  { id: 'overview', label: 'Dashboard', icon: LayoutDashboard },
]

const TAB_TITLES: Record<Tab, { title: string; subtitle: string }> = {
  conversations: { title: 'Chat', subtitle: 'Run agents and monitor streaming responses' },
  agents: { title: 'Agents', subtitle: 'Create, configure, and orchestrate agent graphs' },
  tools: { title: 'Tools', subtitle: 'Catalog of tools available to agents' },
  knowledge: { title: 'Knowledge', subtitle: 'Knowledge bases and documents' },
  models: { title: 'Models', subtitle: 'Model catalog and provider routing' },
  credentials: { title: 'Credentials', subtitle: 'Provider API keys and secrets' },
  overview: { title: 'Dashboard', subtitle: 'Platform status and runtime info' },
}

function getInitialTheme(): 'light' | 'dark' {
  const stored = localStorage.getItem('theme')
  if (stored === 'light' || stored === 'dark') return stored
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function getInitialEditorRailOpen(): boolean {
  try {
    return localStorage.getItem('agent-editor:main-rail') !== '0'
  } catch {
    return true
  }
}

export function App() {
  const [activeTab, setActiveTab] = useState<Tab>('conversations')
  const [theme, setTheme] = useState<'light' | 'dark'>(getInitialTheme)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [editorRailOpen, setEditorRailOpen] = useState(getInitialEditorRailOpen)
  const [editingAgentId, setEditingAgentId] = useState<string | null>(null)
  const [conversations, setConversations] = useState<ConversationItem[]>([])
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null)
  const [state, setState] = useState<HealthState>({
    health: null,
    ready: null,
    error: null,
    loading: true,
  })

  /** Agent editor active: rail sidebar, no global topbar, full-bleed content. */
  const isEditingAgent = activeTab === 'agents' && editingAgentId !== null

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    localStorage.setItem('theme', theme)
  }, [theme])

  useEffect(() => {
    let isMounted = true

    async function checkStatus() {
      try {
        const [healthRes, readyRes] = await Promise.all([fetch('/health'), fetch('/ready')])

        if (!healthRes.ok || !readyRes.ok) {
          throw new Error('Backend health check returned non-200 status')
        }

        const healthData = await healthRes.json()
        const readyData = await readyRes.json()

        if (isMounted) {
          setState({
            health: healthData,
            ready: readyData,
            error: null,
            loading: false,
          })
        }
      } catch (err: any) {
        if (isMounted) {
          setState({
            health: null,
            ready: null,
            error: err.message || 'Failed to reach backend',
            loading: false,
          })
        }
      }
    }

    checkStatus()
    const timer = setInterval(checkStatus, 5000)
    return () => {
      isMounted = false
      clearInterval(timer)
    }
  }, [])

  function toggleEditorRail() {
    setEditorRailOpen((open) => {
      const next = !open
      try {
        localStorage.setItem('agent-editor:main-rail', next ? '1' : '0')
      } catch {
        // Losing a layout preference must not interrupt editing.
      }
      return next
    })
  }

  function selectTab(tab: Tab) {
    setActiveTab(tab)
    setSidebarOpen(false)
    // Leaving the agents tab exits the canvas editor.
    if (tab !== 'agents') setEditingAgentId(null)
  }

  async function loadConversations() {
    try {
      setConversations(await api.listConversations())
    } catch {
      // Sidebar chat history is best-effort; the Chat view surfaces errors.
    }
  }

  useEffect(() => {
    loadConversations()
  }, [])

  function openConversation(id: string) {
    setSelectedConversationId(id)
    setActiveTab('conversations')
    setSidebarOpen(false)
  }

  function startNewConversation() {
    setSelectedConversationId(null)
    setActiveTab('conversations')
    setSidebarOpen(false)
  }

  const tabTitle = TAB_TITLES[activeTab]

  return (
    <div className={`app-shell ${sidebarOpen ? 'sidebar-open' : ''} ${isEditingAgent ? 'app-shell--rail' : ''} ${isEditingAgent && !editorRailOpen ? 'app-shell--rail-hidden' : ''}`}>
      <aside className={`sidebar ${isEditingAgent ? 'sidebar--rail' : ''} ${isEditingAgent && !editorRailOpen ? 'sidebar--rail-hidden' : ''}`} aria-hidden={isEditingAgent && !editorRailOpen} inert={isEditingAgent && !editorRailOpen ? true : undefined}>
        <div className="sidebar-brand">
          <span className="logo-badge" aria-hidden="true">
            <Bot size={18} strokeWidth={1.75} />
          </span>
          <span className="sidebar-brand-name">Agent Orchestrator</span>
          <span className="version-tag">v0.1.0</span>
        </div>

        <nav className="sidebar-nav" aria-label="Primary">
          {PRIMARY_NAV.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`sidebar-item ${activeTab === item.id ? 'active' : ''}`}
              onClick={() => selectTab(item.id)}
              aria-label={isEditingAgent ? item.label : undefined}
              title={isEditingAgent ? item.label : undefined}
            >
              <item.icon aria-hidden="true" />
              {item.label}
            </button>
          ))}
        </nav>

        <div className="sidebar-section-label">Chats</div>
        <div className="sidebar-chats" aria-label="Chat history">
          <button
            type="button"
            className="sidebar-new-chat"
            onClick={startNewConversation}
          >
            <Plus size={14} aria-hidden="true" />
            New chat
          </button>
          {conversations.length === 0 ? (
            <p className="sidebar-chats-empty">No conversations yet</p>
          ) : (
            conversations.map((c) => (
              <button
                key={c.id}
                type="button"
                className={`sidebar-chat-item ${
                  activeTab === 'conversations' && selectedConversationId === c.id ? 'active' : ''
                }`}
                onClick={() => openConversation(c.id)}
                title={c.title || 'Untitled Conversation'}
              >
                <MessageSquare size={13} aria-hidden="true" />
                <span>{c.title || 'Untitled Conversation'}</span>
              </button>
            ))
          )}
        </div>

        <div className="sidebar-section-label">System</div>
        <nav className="sidebar-nav" aria-label="System">
          {SYSTEM_NAV.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`sidebar-item ${activeTab === item.id ? 'active' : ''}`}
              onClick={() => selectTab(item.id)}
              aria-label={isEditingAgent ? item.label : undefined}
              title={isEditingAgent ? item.label : undefined}
            >
              <item.icon aria-hidden="true" />
              {item.label}
            </button>
          ))}
        </nav>

        <div className="sidebar-spacer" />

        <div className="sidebar-footer">
          <div className="status-pill" title="Backend API health">
            <span className={`dot ${state.health?.status === 'ok' ? 'green' : state.loading ? '' : 'red'}`} />
            <span>API {state.health?.status || (state.loading ? 'checking…' : 'down')}</span>
          </div>
          <div className="status-pill" title="Database connection">
            <span className={`dot ${state.ready?.database === 'connected' ? 'green' : state.loading ? '' : 'red'}`} />
            <span>DB {state.ready?.database || (state.loading ? 'checking…' : 'offline')}</span>
          </div>
          <button
            type="button"
            className="theme-toggle"
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {theme === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
            {theme === 'dark' ? 'Light mode' : 'Dark mode'}
          </button>
        </div>
      </aside>

      {isEditingAgent && (
        <button
          type="button"
          className={`editor-main-rail-toggle ${editorRailOpen ? 'open' : ''}`}
          onClick={toggleEditorRail}
          aria-label={editorRailOpen ? 'Hide main sidebar' : 'Show main sidebar'}
          aria-pressed={editorRailOpen}
          title={editorRailOpen ? 'Hide main sidebar' : 'Show main sidebar'}
        >
          <PanelLeft size={14} aria-hidden="true" />
        </button>
      )}

      <div className="sidebar-overlay" onClick={() => setSidebarOpen(false)} aria-hidden="true" />

      <div className="main-area">
        {!isEditingAgent && (
          <header className="topbar">
            <button
              type="button"
              className="mobile-menu-button"
              onClick={() => setSidebarOpen(!sidebarOpen)}
              aria-label={sidebarOpen ? 'Close menu' : 'Open menu'}
            >
              {sidebarOpen ? <X size={20} strokeWidth={1.75} /> : <Menu size={20} strokeWidth={1.75} />}
            </button>
            <div>
              <div className="topbar-title">{tabTitle.title}</div>
              <div className="topbar-subtitle">{tabTitle.subtitle}</div>
            </div>
          </header>
        )}

        <main className={`content ${isEditingAgent ? 'content--bleed' : ''}`}>
          {activeTab === 'conversations' ? (
            <ConversationsView
              selectedConversationId={selectedConversationId}
              onSelectConversation={openConversation}
              onConversationsChanged={loadConversations}
            />
          ) : activeTab === 'agents' ? (
            <AgentsView
              editingAgentId={editingAgentId}
              onEditAgent={setEditingAgentId}
              onExitEditor={() => setEditingAgentId(null)}
              theme={theme}
            />
          ) : activeTab === 'tools' ? (
            <ToolsView />
          ) : activeTab === 'knowledge' ? (
            <KnowledgeView />
          ) : activeTab === 'models' ? (
            <ModelsView />
          ) : activeTab === 'credentials' ? (
            <CredentialsView />
          ) : state.error ? (
            <div className="alert-box error">
              <h3>Backend Offline</h3>
              <p>{state.error}</p>
              <p className="hint">Make sure FastAPI is running on 127.0.0.1:8000</p>
            </div>
          ) : (
            <div className="view-container">
              <div className="card">
                <h2>Platform Ready</h2>
                <p className="subtext">
                  LangChain harness & LangGraph runtime initialized.
                </p>
                <div className="metrics-grid">
                  <div className="metric">
                    <span className="label">Runtime Backend</span>
                    <span className="val highlight">FastAPI + LangGraph</span>
                  </div>
                  <div className="metric">
                    <span className="label">Persistence Checkpointer</span>
                    <span className="val highlight">PostgreSQL 17 + pgvector</span>
                  </div>
                  <div className="metric">
                    <span className="label">Model Gateway</span>
                    <span className="val highlight">LiteLLM In-Process</span>
                  </div>
                </div>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}

export default App
