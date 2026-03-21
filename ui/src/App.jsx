import { useState, useEffect, useCallback } from 'react'
import { api } from './api'
import HostSwitcher from './components/HostSwitcher'
import DbSelector from './components/DbSelector'
import BrowsePanel from './components/BrowsePanel'
import SearchPanel from './components/SearchPanel'
import MemoDetail from './components/MemoDetail'
import MemoEditor from './components/MemoEditor'

function inferDefaultHost() {
  // When served from FastAPI on port 8000, use the same origin
  if (window.location.port === '8000') return window.location.origin
  // Dev mode (vite on 5173) — point to the API server
  return 'http://localhost:8000'
}

const PRESET_HOSTS = [
  { label: 'office', url: inferDefaultHost() },
  { label: 'server4', url: 'http://server4:8000' },
  { label: 'server5', url: 'http://server5:8000' },
]

export default function App() {
  const [hosts] = useState(PRESET_HOSTS)
  const [hostIdx, setHostIdx] = useState(0)
  const [dbPath, setDbPath] = useState(null) // null = global DB

  const [memoList, setMemoList] = useState([])
  const [allTags, setAllTags] = useState([])
  const [listLoading, setListLoading] = useState(false)
  const [listError, setListError] = useState(null)

  const [selectedId, setSelectedId] = useState(null)
  const [selectedMemo, setSelectedMemo] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const [mode, setMode] = useState('browse') // 'browse' | 'search'
  const [editMode, setEditMode] = useState(false)

  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [minScore, setMinScore] = useState(0.4)

  const [tagFilter, setTagFilter] = useState(null)

  const activeHost = hosts[hostIdx]?.url

  const loadList = useCallback(async () => {
    setListLoading(true)
    setListError(null)
    try {
      const data = await api.index(activeHost, dbPath)
      setMemoList(data)
      const tags = [...new Set(data.flatMap((m) => m.tags || []))].sort()
      setAllTags(tags)
    } catch (e) {
      setListError(e.message)
      setMemoList([])
    } finally {
      setListLoading(false)
    }
  }, [activeHost, dbPath])

  // Reload list when host or db changes
  useEffect(() => {
    loadList()
    setSelectedId(null)
    setSelectedMemo(null)
    setEditMode(false)
    setSearchResults([])
    setTagFilter(null)
  }, [activeHost, dbPath]) // eslint-disable-line react-hooks/exhaustive-deps

  // Load full memo when selectedId changes
  useEffect(() => {
    if (!selectedId) { setSelectedMemo(null); return }
    setDetailLoading(true)
    api.get(activeHost, selectedId, dbPath)
      .then(setSelectedMemo)
      .catch(() => setSelectedMemo(null))
      .finally(() => setDetailLoading(false))
  }, [selectedId, activeHost, dbPath])

  const handleSearch = useCallback(async (q) => {
    if (!q.trim()) return
    setSearchLoading(true)
    try {
      const opts = { min_score: minScore }
      if (dbPath) opts.db_path = dbPath
      const results = await api.search(activeHost, q, opts)
      setSearchResults(results)
    } catch {
      setSearchResults([])
    } finally {
      setSearchLoading(false)
    }
  }, [activeHost, dbPath, minScore])

  const handleSelect = (id) => {
    setSelectedId(id)
    setEditMode(false)
  }

  const handleNewMemo = () => {
    setSelectedId(null)
    setSelectedMemo(null)
    setEditMode(true)
  }

  const handleEdit = () => setEditMode(true)

  const handleDelete = async () => {
    if (!selectedMemo) return
    if (!confirm(`Delete "${selectedMemo.title || selectedMemo.id}"?`)) return
    await api.delete(activeHost, selectedMemo.id, dbPath)
    setSelectedId(null)
    setSelectedMemo(null)
    setEditMode(false)
    loadList()
  }

  const handleSave = async (data) => {
    try {
      if (selectedId) {
        const updated = await api.update(activeHost, selectedId, { ...data, db_path: dbPath })
        setSelectedMemo(updated)
      } else {
        const { id } = await api.create(activeHost, { ...data, db_path: dbPath })
        setSelectedId(id)
      }
      setEditMode(false)
      loadList()
    } catch (e) {
      alert('Save failed: ' + e.message)
    }
  }

  const handleCancel = () => {
    setEditMode(false)
    if (!selectedId) setSelectedMemo(null)
  }

  // Escape: cancel editor or deselect memo
  useEffect(() => {
    const handler = (e) => {
      if (e.key !== 'Escape') return
      if (editMode) { handleCancel(); return }
      if (selectedId) { setSelectedId(null); setSelectedMemo(null) }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [editMode, selectedId]) // eslint-disable-line react-hooks/exhaustive-deps

  const filteredList = tagFilter
    ? memoList.filter((m) => m.tags?.includes(tagFilter))
    : memoList

  const showDetail = !editMode && selectedId
  const showEditor = editMode
  const showEmpty = !editMode && !selectedId

  return (
    <div className="flex flex-col h-screen bg-gray-100 font-sans">
      {/* Header */}
      <header className="flex items-center gap-3 px-4 py-2 bg-slate-900 text-white shrink-0 shadow-md">
        <span className="font-bold text-base tracking-wide text-slate-100">memo</span>
        <div className="w-px h-5 bg-slate-600 mx-1" />
        <HostSwitcher hosts={hosts} hostIdx={hostIdx} onSelect={setHostIdx} />
        <div className="ml-auto">
          <DbSelector dbPath={dbPath} onChange={setDbPath} />
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <aside className="w-72 shrink-0 flex flex-col bg-slate-800 overflow-hidden">
          {/* Controls */}
          <div className="flex items-center gap-1 p-2 shrink-0 border-b border-slate-700">
            <button
              onClick={() => setMode('browse')}
              className={`flex-1 text-xs px-2 py-1.5 rounded font-medium transition-colors ${
                mode === 'browse'
                  ? 'bg-slate-600 text-white'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Browse
            </button>
            <button
              onClick={() => setMode('search')}
              className={`flex-1 text-xs px-2 py-1.5 rounded font-medium transition-colors ${
                mode === 'search'
                  ? 'bg-slate-600 text-white'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Search
            </button>
            <button
              onClick={handleNewMemo}
              className="ml-1 text-xs px-3 py-1.5 rounded bg-blue-600 hover:bg-blue-500 text-white font-medium transition-colors"
            >
              + New
            </button>
          </div>

          {/* Panel content */}
          <div className="flex-1 overflow-hidden flex flex-col">
            {mode === 'browse' ? (
              <BrowsePanel
                list={filteredList}
                loading={listLoading}
                error={listError}
                allTags={allTags}
                tagFilter={tagFilter}
                onTagFilter={setTagFilter}
                selectedId={selectedId}
                onSelect={handleSelect}
                onRefresh={loadList}
              />
            ) : (
              <SearchPanel
                query={searchQuery}
                onQueryChange={setSearchQuery}
                onSearch={handleSearch}
                minScore={minScore}
                onMinScoreChange={setMinScore}
                results={searchResults}
                loading={searchLoading}
                selectedId={selectedId}
                onSelect={handleSelect}
              />
            )}
          </div>
        </aside>

        {/* Main panel */}
        <main className="flex-1 overflow-auto bg-white">
          {showEditor && (
            <MemoEditor
              memo={selectedId ? selectedMemo : null}
              onSave={handleSave}
              onCancel={handleCancel}
            />
          )}
          {showDetail && (
            detailLoading ? (
              <div className="p-8 text-gray-400 text-sm">Loading…</div>
            ) : selectedMemo ? (
              <MemoDetail
                memo={selectedMemo}
                onEdit={handleEdit}
                onDelete={handleDelete}
              />
            ) : (
              <div className="p-8 text-gray-400 text-sm">Memo not found.</div>
            )
          )}
          {showEmpty && (
            <div className="flex flex-col items-center justify-center h-full text-gray-300 gap-3">
              <svg className="w-12 h-12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <p className="text-sm">Select a memo or create a new one</p>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
