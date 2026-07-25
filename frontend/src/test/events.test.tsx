import {
  StrictMode,
  type ReactNode,
} from "react"
import { act, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { EventProvider } from "@/features/events/EventProvider"
import {
  useFallbackRefresh,
  useEventConnection,
  useV2Events,
} from "@/features/events/useV2Events"
import type { V2ClientEvent } from "@/api/events"
import { TopBar } from "@/components/layout/TopBar"

class FakeEventSource {
  static instances: FakeEventSource[] = []

  readonly url: string
  closed = false
  private readonly listeners = new Map<string, Set<EventListener>>()

  constructor(url: string | URL) {
    this.url = String(url)
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: EventListener): void {
    const listeners = this.listeners.get(type) ?? new Set<EventListener>()
    listeners.add(listener)
    this.listeners.set(type, listeners)
  }

  removeEventListener(type: string, listener: EventListener): void {
    this.listeners.get(type)?.delete(listener)
  }

  close(): void {
    this.closed = true
  }

  dispatch(type: string, data?: unknown): void {
    const event =
      data === undefined
        ? new Event(type)
        : new MessageEvent(type, { data: JSON.stringify(data) })
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event)
    }
  }
}

function Probe({
  onEvent,
  fallback,
  fallbackActive = false,
}: {
  onEvent: (event: V2ClientEvent) => void
  fallback?: () => void
  fallbackActive?: boolean
}) {
  const connection = useEventConnection()
  useV2Events(undefined, onEvent)
  useFallbackRefresh(fallback ?? (() => undefined), fallbackActive)
  return (
    <output>
      {connection.state}:{String(connection.hasOpened)}
    </output>
  )
}

function Wrapper({ children }: { children: ReactNode }) {
  return <EventProvider>{children}</EventProvider>
}

describe("EventProvider", () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal("EventSource", FakeEventSource)
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it("keeps one live connection under StrictMode", () => {
    const view = render(
      <StrictMode>
        <Wrapper>
          <Probe onEvent={() => undefined} />
        </Wrapper>
      </StrictMode>,
    )

    expect(FakeEventSource.instances).toHaveLength(2)
    expect(FakeEventSource.instances.filter((source) => !source.closed)).toHaveLength(1)
    expect(FakeEventSource.instances.at(-1)?.url).toBe("/api/v2/events")

    view.unmount()
    expect(FakeEventSource.instances.every((source) => source.closed)).toBe(true)
  })

  it("dispatches validated named events and removes unmounted subscribers", () => {
    const events: V2ClientEvent[] = []
    const view = render(
      <Wrapper>
        <Probe onEvent={(event) => events.push(event)} />
      </Wrapper>,
    )
    const source = FakeEventSource.instances[0]

    act(() => {
      source.dispatch("open")
      source.dispatch("episode.updated", {
        episodeId: "episode-1",
        status: "processing",
        progress: 40,
      })
      source.dispatch("episode.deleted", { episodeId: 3 })
    })

    expect(events.map((event) => event.type)).toEqual([
      "reconnected",
      "episode.updated",
    ])
    expect(screen.getByText("open:true")).toBeInTheDocument()

    view.unmount()
    source.dispatch("dashboard.invalidated", {})
    expect(events).toHaveLength(2)
  })

  it("exposes closed state after an EventSource error", () => {
    render(
      <Wrapper>
        <Probe onEvent={() => undefined} />
      </Wrapper>,
    )
    const source = FakeEventSource.instances[0]

    act(() => source.dispatch("open"))
    expect(screen.getByText("open:true")).toBeInTheDocument()
    act(() => source.dispatch("error"))
    expect(screen.getByText("closed:true")).toBeInTheDocument()
  })

  it("shows reconnecting status only after the first successful open", () => {
    render(
      <Wrapper>
        <TopBar />
      </Wrapper>,
    )
    const source = FakeEventSource.instances[0]

    expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument()
    act(() => source.dispatch("error"))
    expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument()
    act(() => source.dispatch("open"))
    expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument()
    act(() => source.dispatch("error"))
    expect(screen.getByText("Reconnecting…")).toBeInTheDocument()
  })

  it("falls back every 30 seconds only while disconnected and visible", () => {
    vi.useFakeTimers()
    const refresh = vi.fn()
    render(
      <Wrapper>
        <Probe
          onEvent={() => undefined}
          fallback={refresh}
          fallbackActive
        />
      </Wrapper>,
    )
    const source = FakeEventSource.instances[0]

    act(() => vi.advanceTimersByTime(30_000))
    expect(refresh).toHaveBeenCalledTimes(1)

    act(() => source.dispatch("open"))
    act(() => vi.advanceTimersByTime(60_000))
    expect(refresh).toHaveBeenCalledTimes(1)

    act(() => source.dispatch("error"))
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    })
    act(() => document.dispatchEvent(new Event("visibilitychange")))
    act(() => vi.advanceTimersByTime(60_000))
    expect(refresh).toHaveBeenCalledTimes(1)

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    })
    act(() => document.dispatchEvent(new Event("visibilitychange")))
    expect(refresh).toHaveBeenCalledTimes(2)
  })
})
