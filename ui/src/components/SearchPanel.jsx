import { useRef, useEffect } from 'react'
import { Tag, relativeTime } from './MemoCard'

export default function SearchPanel({
  query, onQueryChange, onSearch,
  minScore, onMinScoreChange,
  results, loading, selectedId, onSelect,
}) {
  const inputRef = useRef(null)

  // Auto-focus when panel mounts
  useEffect(() => { inputRef.current?.focus() }, [])

  const handleSubmit = (e) => {
    e.preventDefault()
    onSearch(query)
  }

  return (
    <div className="flex flex-col overflow-hidden flex-1">
      <form onSubmit={handleSubmit} className="p-2 shrink-0 border-b border-slate-700">
        <div className="flex gap-1">
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder="Describe what you're looking for…"
            className="flex-1 text-sm px-2.5 py-1.5 bg-slate-700 text-slate-200 border border-slate-600 rounded placeholder:text-slate-500 focus:outline-none focus:border-blue-500"
          />
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="text-xs px-3 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded transition-colors font-medium shrink-0"
          >
            {loading ? '…' : 'Search'}
          </button>
        </div>
        <div className="flex items-center gap-2 mt-2 px-0.5">
          <span className="text-xs text-slate-400 shrink-0">min score</span>
          <input
            type="range" min="0" max="1" step="0.05"
            value={minScore}
            onChange={(e) => onMinScoreChange(parseFloat(e.target.value))}
            className="flex-1 accent-blue-500"
          />
          <span className="text-xs text-slate-400 w-8 text-right tabular-nums">{minScore.toFixed(2)}</span>
        </div>
      </form>

      <div className="overflow-y-auto flex-1">
        {loading && (
          <div className="px-3 py-6 text-xs text-slate-500 text-center">Searching…</div>
        )}
        {!loading && results.length === 0 && query && (
          <div className="px-3 py-8 text-xs text-slate-500 text-center">
            <p>No results above {minScore.toFixed(2)}</p>
            <p className="mt-1 text-slate-600">Try lowering the min score</p>
          </div>
        )}
        {!loading && results.length === 0 && !query && (
          <div className="px-3 py-8 text-xs text-slate-500 text-center">
            Enter a query to search
          </div>
        )}
        {results.map(({ document: doc, score }) => (
          <button
            key={doc.id}
            onClick={() => onSelect(doc.id)}
            className={`w-full text-left px-3 py-2.5 border-b border-slate-700 transition-colors ${
              doc.id === selectedId ? 'bg-slate-600' : 'hover:bg-slate-700'
            }`}
          >
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium truncate text-slate-200 flex-1">
                {doc.title || doc.id.slice(0, 12) + '…'}
              </p>
              <span className={`text-xs px-1.5 py-0.5 rounded shrink-0 font-mono tabular-nums ${
                score >= 0.7 ? 'bg-green-700/60 text-green-300' :
                score >= 0.5 ? 'bg-blue-700/60 text-blue-300' :
                'bg-slate-600 text-slate-400'
              }`}>
                {score.toFixed(2)}
              </span>
            </div>
            {(doc.tags || []).length > 0 && (
              <div className="flex flex-wrap gap-1 mt-1.5">
                {doc.tags.slice(0, 3).map((t) => <Tag key={t} name={t} />)}
                {doc.tags.length > 3 && (
                  <span className="text-xs text-slate-500 self-center">+{doc.tags.length - 3}</span>
                )}
              </div>
            )}
            {doc.created_at && (
              <p className="text-xs text-slate-500 mt-1">{relativeTime(doc.created_at)}</p>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}
