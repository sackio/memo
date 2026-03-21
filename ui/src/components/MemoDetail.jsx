import { useState } from 'react'
import { Tag } from './MemoCard'

function fmtDate(ts) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <button
      onClick={copy}
      title="Copy content"
      className="text-xs px-2 py-1 rounded border border-gray-200 text-gray-400 hover:text-gray-600 hover:border-gray-300 transition-colors"
    >
      {copied ? '✓ Copied' : 'Copy'}
    </button>
  )
}

export default function MemoDetail({ memo, onEdit, onDelete }) {
  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-5 py-2.5 border-b border-gray-200 shrink-0 bg-gray-50">
        <button
          onClick={onEdit}
          className="text-sm px-3 py-1.5 rounded bg-blue-600 hover:bg-blue-500 text-white font-medium transition-colors"
        >
          Edit
        </button>
        <button
          onClick={onDelete}
          className="text-sm px-3 py-1.5 rounded border border-red-200 text-red-500 hover:bg-red-50 font-medium transition-colors"
        >
          Delete
        </button>
        <div className="ml-auto flex items-center gap-3">
          <CopyButton text={memo.content} />
          {memo.token_count && (
            <span className="text-xs text-gray-400">{memo.token_count.toLocaleString()} tokens</span>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-5">
        {/* Title */}
        <h1 className="text-xl font-semibold text-gray-900 mb-2 leading-tight">
          {memo.title || <span className="text-gray-400 italic font-normal">Untitled</span>}
        </h1>

        {/* Tags */}
        {(memo.tags || []).length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-3">
            {memo.tags.map((t) => <Tag key={t} name={t} />)}
          </div>
        )}

        {/* Dates + ID */}
        <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-gray-400 mb-5 pb-4 border-b border-gray-100">
          <span>Created {fmtDate(memo.created_at)}</span>
          {memo.updated_at && memo.updated_at !== memo.created_at && (
            <span>Edited {fmtDate(memo.updated_at)}</span>
          )}
          <span className="font-mono text-gray-300" title={memo.id}>{memo.id?.slice(0, 8)}…</span>
        </div>

        {/* Body */}
        <pre className="text-sm text-gray-800 whitespace-pre-wrap font-mono leading-relaxed bg-gray-50 rounded-lg p-4 border border-gray-100 overflow-x-auto">
          {memo.content}
        </pre>

        {/* Metadata */}
        {memo.metadata && Object.keys(memo.metadata).length > 0 && (
          <details className="mt-4">
            <summary className="text-xs text-gray-400 cursor-pointer hover:text-gray-600 select-none">
              Metadata ({Object.keys(memo.metadata).length} keys)
            </summary>
            <pre className="mt-2 text-xs text-gray-600 bg-gray-50 rounded-lg p-3 border border-gray-100 whitespace-pre-wrap">
              {JSON.stringify(memo.metadata, null, 2)}
            </pre>
          </details>
        )}
      </div>
    </div>
  )
}
