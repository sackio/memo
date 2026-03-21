import { useState, useEffect, useRef } from 'react'
import { Tag } from './MemoCard'

function parseTags(str) {
  return str.split(',').map((t) => t.trim()).filter(Boolean)
}

export default function MemoEditor({ memo, onSave, onCancel }) {
  const [title, setTitle] = useState(memo?.title || '')
  const [content, setContent] = useState(memo?.content || '')
  const [tagInput, setTagInput] = useState((memo?.tags || []).join(', '))
  const [saving, setSaving] = useState(false)
  const contentRef = useRef(null)
  const isEdit = !!memo

  useEffect(() => {
    setTitle(memo?.title || '')
    setContent(memo?.content || '')
    setTagInput((memo?.tags || []).join(', '))
  }, [memo?.id])

  useEffect(() => {
    const target = isEdit ? contentRef.current : contentRef.current
    if (target) { target.focus(); if (!isEdit) target.setSelectionRange(0, 0) }
  }, [memo?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Ctrl/Cmd+S to save
  useEffect(() => {
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault()
        if (content.trim() && !saving) document.getElementById('memo-save-btn')?.click()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [content, saving])

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!content.trim()) { alert('Content is required.'); return }
    setSaving(true)
    try {
      await onSave({
        content: content.trim(),
        title: title.trim() || null,
        tags: parseTags(tagInput),
      })
    } finally {
      setSaving(false)
    }
  }

  const tags = parseTags(tagInput)
  const wordCount = content.trim() ? content.trim().split(/\s+/).length : 0

  return (
    <form onSubmit={handleSubmit} className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-5 py-2.5 border-b border-gray-200 shrink-0 bg-gray-50">
        <span className="text-sm font-medium text-gray-600">
          {isEdit ? 'Edit memo' : 'New memo'}
        </span>
        <div className="flex gap-2 ml-auto items-center">
          <span className="text-xs text-gray-400 hidden sm:block">
            ⌘S to save
          </span>
          <button
            type="button"
            onClick={onCancel}
            className="text-sm px-3 py-1.5 rounded border border-gray-200 text-gray-600 hover:bg-gray-100 font-medium transition-colors"
          >
            Cancel
          </button>
          <button
            id="memo-save-btn"
            type="submit"
            disabled={saving || !content.trim()}
            className="text-sm px-4 py-1.5 rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white font-medium transition-colors"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>

      {/* Form body */}
      <div className="flex-1 overflow-y-auto px-6 py-5 flex flex-col gap-4">
        {/* Title */}
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1 uppercase tracking-wide">Title</label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Optional title"
            className="w-full text-lg px-0 py-1 border-0 border-b border-gray-200 focus:outline-none focus:border-blue-400 text-gray-900 placeholder:text-gray-300 bg-transparent"
          />
        </div>

        {/* Content */}
        <div className="flex-1 flex flex-col">
          <div className="flex items-center justify-between mb-1">
            <label className="text-xs font-medium text-gray-400 uppercase tracking-wide">
              Content <span className="text-red-400 normal-case">*</span>
            </label>
            {wordCount > 0 && (
              <span className="text-xs text-gray-300">{wordCount} words</span>
            )}
          </div>
          <textarea
            ref={contentRef}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="Write your memo here…"
            className="flex-1 min-h-64 w-full text-sm px-3 py-2.5 border border-gray-200 rounded-lg focus:outline-none focus:border-blue-400 text-gray-900 placeholder:text-gray-300 font-mono leading-relaxed resize-none"
          />
        </div>

        {/* Tags */}
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1 uppercase tracking-wide">Tags</label>
          <input
            type="text"
            value={tagInput}
            onChange={(e) => setTagInput(e.target.value)}
            placeholder="comma, separated, tags"
            className="w-full text-sm px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:border-blue-400 text-gray-700 placeholder:text-gray-300"
          />
          {tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {tags.map((t) => <Tag key={t} name={t} />)}
            </div>
          )}
        </div>
      </div>
    </form>
  )
}
