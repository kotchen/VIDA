import { Captions, FileCode, FileText } from "lucide-react"
import { Card } from "@/components/ui/card"

const FORMATS = [
  { icon: FileText, name: "TXT", ext: ".txt" },
  { icon: Captions, name: "SRT", ext: ".srt" },
  { icon: FileCode, name: "MD", ext: ".md" },
]

export function ExportCard() {
  return (
    <Card className="card-glow flex h-full flex-col gap-3 rounded-2xl border-warm/60 bg-card p-4">
      <div>
        <h2 className="text-base font-semibold text-gold">Export</h2>
        <p className="text-xs text-muted-warm">Export your transcript or summary</p>
      </div>
      <div className="grid flex-1 grid-cols-3 gap-3">
        {FORMATS.map(({ icon: Icon, name, ext }) => (
          <button
            key={name}
            className="flex flex-col items-center justify-center gap-1.5 rounded-xl border border-warm/60 bg-raised transition-colors hover:border-copper-500/60"
          >
            <Icon className="size-6 text-copper-300" strokeWidth={1.5} />
            <span className="text-sm font-semibold">{name}</span>
            <span className="text-[10px] text-muted-warm">{ext}</span>
          </button>
        ))}
      </div>
      <p className="text-center text-[10px] text-muted-warm/70">+ More formats coming soon</p>
    </Card>
  )
}
