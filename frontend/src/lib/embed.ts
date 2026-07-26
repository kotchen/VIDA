/**
 * Build a network embed player descriptor for supported platform pages so
 * URL Episodes play the original video instead of the downloaded audio
 * track. Returns null when the page is not embeddable (caller falls back to
 * the locally committed media).
 *
 * The returned `url` is the base embed URL without any start-time/seek
 * parameter; callers append provider-specific seek parameters when needed:
 * - bilibili: `&t=<sec>&autoplay=1` (no cross-origin seek API exists, so a
 *   seek reloads the iframe with a start time)
 * - youtube: the base URL already carries `enablejsapi=1` so the player
 *   accepts `postMessage` `seekTo` commands without a reload.
 */
export type NetworkEmbed = {
  provider: "bilibili" | "youtube"
  url: string
}

export function networkEmbed(sourceUrl: string): NetworkEmbed | null {
  let url: URL
  try {
    url = new URL(sourceUrl)
  } catch {
    return null
  }
  const host = url.hostname.toLowerCase().replace(/\.$/, "")
  if (host === "bilibili.com" || host.endsWith(".bilibili.com")) {
    const bvid = url.pathname.match(/\/video\/(BV[0-9A-Za-z]+)/)?.[1]
    return bvid
      ? {
          provider: "bilibili",
          url: `https://player.bilibili.com/player.html?bvid=${bvid}`,
        }
      : null
  }
  if (host === "youtu.be") {
    const id = url.pathname.split("/").filter(Boolean)[0]
    return id
      ? {
          provider: "youtube",
          url: `https://www.youtube-nocookie.com/embed/${id}?enablejsapi=1`,
        }
      : null
  }
  if (host === "youtube.com" || host.endsWith(".youtube.com")) {
    const id =
      url.searchParams.get("v") ??
      url.pathname.match(/\/(?:shorts|embed|live)\/([\w-]+)/)?.[1]
    return id
      ? {
          provider: "youtube",
          url: `https://www.youtube-nocookie.com/embed/${id}?enablejsapi=1`,
        }
      : null
  }
  return null
}
