import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react"

import {
  V2_EVENT_TYPES,
  parseV2Event,
  type V2ClientEvent,
} from "@/api/events"
import {
  EventContext,
  type EventConnectionState,
  type EventSubscriber,
} from "@/features/events/EventContext"

export function EventProvider({ children }: { children: ReactNode }) {
  const subscribers = useRef(new Set<EventSubscriber>())
  const [state, setState] = useState<EventConnectionState>("connecting")
  const [hasOpened, setHasOpened] = useState(false)

  const subscribe = useCallback((subscriber: EventSubscriber) => {
    subscribers.current.add(subscriber)
    return () => subscribers.current.delete(subscriber)
  }, [])

  const dispatch = useCallback((event: V2ClientEvent) => {
    for (const subscriber of [...subscribers.current]) {
      subscriber(event)
    }
  }, [])

  useEffect(() => {
    setState("connecting")
    let source: EventSource
    try {
      source = new EventSource("/api/v2/events")
    } catch {
      setState("closed")
      return
    }

    const handleOpen = () => {
      setState("open")
      setHasOpened(true)
      dispatch({ type: "reconnected", data: {} })
    }
    const handleError = () => setState("closed")
    const eventListeners = V2_EVENT_TYPES.map((type) => {
      const listener: EventListener = (event) => {
        if (!(event instanceof MessageEvent)) return
        const parsed = parseV2Event(type, String(event.data))
        if (parsed !== null) dispatch(parsed)
      }
      source.addEventListener(type, listener)
      return [type, listener] as const
    })
    source.addEventListener("open", handleOpen)
    source.addEventListener("error", handleError)

    return () => {
      source.removeEventListener("open", handleOpen)
      source.removeEventListener("error", handleError)
      for (const [type, listener] of eventListeners) {
        source.removeEventListener(type, listener)
      }
      source.close()
    }
  }, [dispatch])

  const value = useMemo(
    () => ({ state, hasOpened, subscribe }),
    [hasOpened, state, subscribe],
  )
  return <EventContext.Provider value={value}>{children}</EventContext.Provider>
}
