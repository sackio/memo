import { useState } from 'react'

export default function DbSelector({ dbPath, onChange }) {
  const [input, setInput] = useState(dbPath || '')
  const [open, setOpen] = useState(false)
  const isCustom = dbPath != null

  const applyCustom = () => {
    const val = input.trim()
    onChange(val || null)
    setOpen(false)
  }

  const useGlobal = () => {
    setInput('')
    onChange(null)
    setOpen(false)
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded font-medium transition-colors ${
          isCustom
            ? 'bg-amber-600 text-white'
            : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700'
        }`}
      >
        <span>{isCustom ? `db: ${dbPath}` : 'global db'}</span>
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 w-72 bg-slate-800 border border-slate-600 rounded-lg shadow-xl z-50 p-3">
          <p className="text-xs text-slate-400 mb-2">Database</p>
          <button
            onClick={useGlobal}
            className={`w-full text-left text-xs px-3 py-2 rounded mb-2 transition-colors ${
              !isCustom ? 'bg-blue-600 text-white' : 'text-slate-300 hover:bg-slate-700'
            }`}
          >
            Global DB (default)
          </button>
          <div className="flex gap-2">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && applyCustom()}
              placeholder="/mnt/db/project or /data/project"
              className="flex-1 text-xs px-2 py-1.5 bg-slate-700 text-slate-200 border border-slate-600 rounded placeholder:text-slate-500 focus:outline-none focus:border-blue-500"
            />
            <button
              onClick={applyCustom}
              className="text-xs px-2 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded transition-colors"
            >
              Set
            </button>
          </div>
          <p className="text-xs text-slate-500 mt-1.5">
            Path on the server's filesystem. Directory or .db file.
          </p>
        </div>
      )}
    </div>
  )
}
