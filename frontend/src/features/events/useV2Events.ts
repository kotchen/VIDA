import { useEffect, useRef } from "react"

import type { V2ClientEvent } from "@/api/events"
import {
  useEventContext,
  type EventContextValue,
} from "@/features/events/EventContext"

export type V2EventFilter = (event: V2ClientEvent) => boolean

export function useEventConnection(): Pick<
  EventContextValue,
  "state" | "hasOpened"
> {
  const context = useEventContext()
  return { state: context.state, hasOpened: context.hasOpened }
}

export function useV2Events(
  filter: V2EventFilter | undefined,
  callback: (event: V2ClientEvent) => void,
): void {
  const { subscribe } = useEventContext()
  const callbackRef = useRef(callback)
  const filterRef = useRef(filter)
  callbackRef.current = callback
  filterRef.current = filter

  useEffect(
    () =>
      subscribe((event) => {
        if (filterRef.current?.(event) ?? true) {
          callbackRef.current(event)
        }
      }),
    [subscribe],
  )
}

export function useFallbackRefresh(
  callback: () => void,
  active: boolean,
): void {
  const { state } = useEventContext()
  const callbackRef = useRef(callback)
  callbackRef.current = callback

  useEffect(() => {
    if (!active || state === "open") return

    let timer: ReturnType<typeof setInterval> | undefined
    let wasVisible = document.visibilityState === "visible"
    const syncTimer = () => {
      if (timer !== undefined) clearInterval(timer)
      timer =
        document.visibilityState === "visible"
          ? setInterval(() => callbackRef.current(), 30_000)
          : undefined
    }
    const handleVisibility = () => {
      const isVisible = document.visibilityState === "visible"
      if (isVisible && !wasVisible) callbackRef.current()
      wasVisible = isVisible
      syncTimer()
    }

    syncTimer()
    document.addEventListener("visibilitychange", handleVisibility)
    return () => {
      if (timer !== undefined) clearInterval(timer)
      document.removeEventListener("visibilitychange", handleVisibility)
    }
  }, [active, state])
}
