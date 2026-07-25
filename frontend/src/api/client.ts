const MAX_ERROR_BODY_CHARS = 64 * 1024

interface ErrorEnvelope {
  error?: {
    code?: unknown
    message?: unknown
    details?: unknown
    requestId?: unknown
  }
}

export interface ApiRequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown
}

export interface ApiBlob {
  blob: Blob
  contentDisposition: string | null
  requestId: string | null
}

export class ApiError extends Error {
  readonly httpStatus: number
  readonly code: string
  readonly details: Record<string, unknown>
  readonly requestId: string | null

  constructor(
    httpStatus: number,
    code: string,
    message: string,
    details: Record<string, unknown> = {},
    requestId: string | null = null,
  ) {
    super(message)
    this.name = "ApiError"
    this.httpStatus = httpStatus
    this.code = code
    this.details = details
    this.requestId = requestId
  }
}

export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const response = await request(path, options)
  if (response.status === 204) {
    return undefined as T
  }
  return response.json() as Promise<T>
}

export async function apiBlob(
  path: string,
  options: ApiRequestOptions = {},
): Promise<ApiBlob> {
  const response = await request(path, options)
  return {
    blob: await response.blob(),
    contentDisposition: response.headers.get("Content-Disposition"),
    requestId: response.headers.get("X-Request-ID"),
  }
}

async function request(
  path: string,
  options: ApiRequestOptions,
): Promise<Response> {
  assertRelativePath(path)
  const { body, headers: inputHeaders, ...init } = options
  const headers = new Headers(inputHeaders)
  let requestBody: BodyInit | null | undefined
  if (body instanceof FormData || body instanceof Blob || typeof body === "string") {
    requestBody = body
  } else if (body !== undefined) {
    headers.set("Content-Type", "application/json")
    requestBody = JSON.stringify(body)
  }

  let response: Response
  try {
    response = await fetch(path, { ...init, headers, body: requestBody })
  } catch {
    throw new ApiError(0, "network_error", "Unable to reach the server")
  }
  if (!response.ok) {
    throw await responseError(response)
  }
  return response
}

async function responseError(response: Response): Promise<ApiError> {
  const headerRequestId = response.headers.get("X-Request-ID")
  let envelope: ErrorEnvelope = {}
  try {
    const text = (await response.text()).slice(0, MAX_ERROR_BODY_CHARS)
    envelope = JSON.parse(text) as ErrorEnvelope
  } catch {
    // The normalized fallback below intentionally discards malformed server text.
  }
  const error = envelope.error
  const code = typeof error?.code === "string" ? error.code : "http_error"
  const message =
    typeof error?.message === "string" ? error.message : "Request failed"
  const details =
    error?.details !== null &&
    typeof error?.details === "object" &&
    !Array.isArray(error.details)
      ? (error.details as Record<string, unknown>)
      : {}
  const bodyRequestId =
    typeof error?.requestId === "string" ? error.requestId : null
  return new ApiError(
    response.status,
    code,
    message,
    details,
    headerRequestId ?? bodyRequestId,
  )
}

function assertRelativePath(path: string): void {
  if (!path.startsWith("/") || path.startsWith("//")) {
    throw new ApiError(0, "invalid_api_path", "API path must be relative")
  }
}
