import { apiRequest } from "@/api/client"
import type {
  ProviderConnectionTest,
  ProviderModelDiscovery,
  ProviderModelDiscoveryInput,
  ProviderProfile,
  ProviderProfileCreateInput,
  ProviderProfileUpdateInput,
} from "@/api/types"

export const profilesApi = {
  list(signal?: AbortSignal): Promise<ProviderProfile[]> {
    return apiRequest("/api/v2/provider-profiles", { signal })
  },

  get(id: string, signal?: AbortSignal): Promise<ProviderProfile> {
    return apiRequest(`/api/v2/provider-profiles/${segment(id)}`, { signal })
  },

  create(input: ProviderProfileCreateInput): Promise<ProviderProfile> {
    return apiRequest("/api/v2/provider-profiles", {
      method: "POST",
      body: input,
    })
  },

  update(
    id: string,
    input: ProviderProfileUpdateInput,
  ): Promise<ProviderProfile> {
    return apiRequest(`/api/v2/provider-profiles/${segment(id)}`, {
      method: "PATCH",
      body: input,
    })
  },

  delete(id: string): Promise<void> {
    return apiRequest(`/api/v2/provider-profiles/${segment(id)}`, {
      method: "DELETE",
    })
  },

  test(id: string): Promise<ProviderConnectionTest> {
    return apiRequest(`/api/v2/provider-profiles/${segment(id)}/test`, {
      method: "POST",
    })
  },

  discoverModels(
    input: ProviderModelDiscoveryInput,
    signal?: AbortSignal,
  ): Promise<ProviderModelDiscovery> {
    return apiRequest("/api/v2/provider-profiles/models", {
      method: "POST",
      body: input,
      signal,
    })
  },
}

function segment(value: string): string {
  return encodeURIComponent(value)
}
