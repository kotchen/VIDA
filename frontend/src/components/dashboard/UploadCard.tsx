import { CloudUpload } from "lucide-react"
import { Card } from "@/components/ui/card"

export function UploadCard() {
  return (
    <Card className="card-glow flex h-full flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed border-copper-500/50 bg-card p-6 text-center">
      <CloudUpload className="size-10 text-copper-300" strokeWidth={1.5} />
      <p className="text-base font-semibold">Upload a video or audio file</p>
      <p className="text-sm text-muted-warm">Drag &amp; drop a file here, or click to browse</p>
      <p className="text-xs text-muted-warm/70">MP4, MOV, MKV, AVI, MP3, M4A · Up to 5GB</p>
    </Card>
  )
}
