export default function HostSwitcher({ hosts, hostIdx, onSelect }) {
  return (
    <div className="flex items-center gap-1">
      {hosts.map((h, i) => (
        <button
          key={h.url}
          onClick={() => onSelect(i)}
          className={`text-xs px-2.5 py-1 rounded font-medium transition-colors ${
            i === hostIdx
              ? 'bg-blue-600 text-white'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700'
          }`}
        >
          {h.label}
        </button>
      ))}
    </div>
  )
}
