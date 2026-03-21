import { useState, useRef, useEffect } from 'react'
import MemoCard from './MemoCard'

function TagDropdown({ allTags, tagFilter, onTagFilter }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const handler = (e) => { if (!ref.current?.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const select = (t) => { onTagFilter(t === tagFilter ? null : t); setOpen(false) }

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-1 text-xs px-2 py-1 rounded transition-colors ${
          tagFilter
            ? 'bg-blue-600 text-white'
            : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700'
        }`}
      >
        {tagFilter ? `# ${tagFilter}` : 'Filter by tag'}
        <svg className="w-3 h-3 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={open ? 'M5 15l7-7 7 7' : 'M19 9l-7 7-7-7'} />
        </svg>
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1 w-56 bg-slate-700 border border-slate-600 rounded-lg shadow-xl z-50 overflow-hidden">
          <div className="max-h-64 overflow-y-auto py-1">
            {tagFilter && (
              <button
                onClick={() => { onTagFilter(null); setOpen(false) }}
                className="w-full text-left text-xs px-3 py-1.5 text-slate-300 hover:bg-slate-600 border-b border-slate-600"
              >
                ✕ Clear filter
              </button>
            )}
            {allTags.map((t) => (
              <button
                key={t}
                onClick={() => select(t)}
                className={`w-full text-left text-xs px-3 py-1.5 transition-colors ${
                  t === tagFilter
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-300 hover:bg-slate-600'
                }`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default function BrowsePanel({
  list, loading, error, allTags, tagFilter,
  onTagFilter, selectedId, onSelect, onRefresh,
}) {
  const count = list.length
  const countLabel = loading ? '…' : error ? 'error' : `${count} memo${count !== 1 ? 's' : ''}${tagFilter ? ' filtered' : ''}`

  return (
    <div className="flex flex-col overflow-hidden flex-1">
      {/* Filter + count bar */}
      <div className="flex items-center justify-between gap-2 px-2 py-1.5 shrink-0 border-b border-slate-700">
        {allTags.length > 0
          ? <TagDropdown allTags={allTags} tagFilter={tagFilter} onTagFilter={onTagFilter} />
          : <span className="text-xs text-slate-500">No tags</span>
        }
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">{countLabel}</span>
          <button
            onClick={onRefresh}
            className="text-slate-500 hover:text-slate-300 transition-colors"
            title="Refresh"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>
        </div>
      </div>

      {/* List */}
      <div className="overflow-y-auto flex-1">
        {error && (
          <div className="px-3 py-4 text-xs text-red-400">{error}</div>
        )}
        {!loading && !error && list.length === 0 && (
          <div className="px-3 py-8 text-xs text-slate-500 text-center">
            {tagFilter ? `No memos tagged "${tagFilter}"` : 'No memos yet'}
          </div>
        )}
        {list.map((memo) => (
          <MemoCard
            key={memo.id}
            memo={memo}
            selected={memo.id === selectedId}
            onClick={() => onSelect(memo.id)}
          />
        ))}
      </div>
    </div>
  )
}
