import { useMemo, useState } from "react"
import { Link } from "react-router"
import { Button } from "@/components/ui/button"
import { useLibrary } from "@/features/library/useLibrary"
import type { EpisodeStatus } from "@/api/types"

export function LibraryPage() {
  const state = useLibrary()
  const [query, setQuery] = useState("")
  const [status, setStatus] = useState<"all" | EpisodeStatus>("all")
  const rows = useMemo(() => state.projects.filter((row) =>
    (status === "all" || row.status === status) &&
    row.title.toLowerCase().includes(query.toLowerCase()),
  ), [query, state.projects, status])
  return <div className="p-8">
    <h1 className="font-display text-3xl text-gold">Library</h1>
    <input aria-label="Filter projects" value={query} onChange={(e) => setQuery(e.target.value)} />
    <select aria-label="Filter status" value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
      <option value="all">All statuses</option>
      {["queued","processing","completed","failed","canceled"].map((value) => <option key={value}>{value}</option>)}
    </select>
    <div className="grid gap-3">{rows.map((row) => <article key={row.id}>
      <Link to={`/episodes/${encodeURIComponent(row.id)}`}>{row.title}</Link>
      <span>{row.status}</span>
      {(row.status === "queued" || row.status === "processing") ? <Button onClick={() => void state.cancel(row.id)}>Cancel</Button> : null}
      {["completed","failed","canceled"].includes(row.status) ? <Button variant="destructive" onClick={() => void state.remove(row.id)}>Delete</Button> : null}
    </article>)}</div>
    {state.hasMore ? <Button onClick={() => void state.loadMore()}>Load more</Button> : null}
  </div>
}
