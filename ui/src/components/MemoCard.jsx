const TAG_COLORS = [
  'bg-blue-500', 'bg-green-500', 'bg-purple-500',
  'bg-orange-500', 'bg-pink-500', 'bg-teal-500', 'bg-cyan-500',
]

function tagColor(tag) {
  let h = 0
  for (let i = 0; i < tag.length; i++) h = ((h << 5) - h + tag.charCodeAt(i)) | 0
  return TAG_COLORS[Math.abs(h) % TAG_COLORS.length]
}

export function Tag({ name, onClick }) {
  const cls = `inline-block text-white text-xs px-1.5 py-0.5 rounded-full ${tagColor(name)}`
  return onClick
    ? <button type="button" onClick={onClick} className={cls}>{name}</button>
    : <span className={cls}>{name}</span>
}

export function relativeTime(ts) {
  if (!ts) return ''
  const diff = Date.now() - ts * 1000
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(diff / 3600000)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(diff / 86400000)
  if (d < 30) return `${d}d ago`
  return new Date(ts * 1000).toLocaleDateString()
}

export default function MemoCard({ memo, selected, onClick }) {
  const title = memo.title || memo.id?.slice(0, 12) + '…'
  const tags = memo.tags || []

  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 border-b border-slate-700 transition-colors ${
        selected ? 'bg-slate-600' : 'hover:bg-slate-700'
      }`}
    >
      <div className="flex items-start justify-between gap-1">
        <p className={`text-sm font-medium truncate leading-snug ${selected ? 'text-white' : 'text-slate-200'}`}>
          {title}
        </p>
        {memo.token_count && (
          <span className="text-xs text-slate-500 shrink-0 mt-0.5">{memo.token_count}t</span>
        )}
      </div>
      {tags.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1.5">
          {tags.slice(0, 3).map((t) => <Tag key={t} name={t} />)}
          {tags.length > 3 && (
            <span className="text-xs text-slate-500 self-center">+{tags.length - 3}</span>
          )}
        </div>
      )}
      {memo.created_at && (
        <p className="text-xs text-slate-500 mt-1">{relativeTime(memo.created_at)}</p>
      )}
    </button>
  )
}
