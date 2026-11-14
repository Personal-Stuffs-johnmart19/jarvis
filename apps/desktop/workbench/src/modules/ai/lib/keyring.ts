import { t } from "../../../../../src/localization";
import { getJarvis } from "@/jarvis";
import { invoke } from "@/host/core";
import {
  getProvider,
  KEYRING_SERVICE,
  PROVIDERS,
  providerSupportsKey,
  type CustomEndpoint,
  type ProviderId,
} from "../config";

export type ProviderKeys = Record<ProviderId, string | null>;
export type CustomEndpointKeys = Record<string, string | null>;

export const EMPTY_PROVIDER_KEYS: ProviderKeys = {
  openai: null,
  anthropic: null,
  google: null,
  xai: null,
  cerebras: null,
  groq: null,
  deepseek: null,
  mistral: null,
  openrouter: null,
  "openai-compatible": null,
  lmstudio: null,
  mlx: null,
  ollama: null,
};

export async function getKey(provider: ProviderId): Promise<string | null> {
  if (provider === "google") {
    const bridge = await getJarvis();
    if (bridge.base) {
      const snapshot = await invoke<{key_configured:boolean}>("backend", {method:"snapshot", params:{}});
      return snapshot.key_configured ? bridge.token : null;
    }
  }
  if (!providerSupportsKey(provider)) return null;
  try {
    const v = await invoke<string | null>("secrets_get", {
      service: KEYRING_SERVICE,
      account: getProvider(provider).keyringAccount,
    });
    return v && v.length > 0 ? v : null;
  } catch {
    return null;
  }
}

export async function setKey(provider: ProviderId, key: string): Promise<void> {
  if (provider === "google" && (await getJarvis()).base) {
    if (!key.trim()) throw new Error("API key is empty");
    await invoke("backend", {method:"settings.key", params:{key:key.trim()}});
    return;
  }
  if (!providerSupportsKey(provider)) {
    throw new Error(`${provider} does not use an API key`);
  }
  const trimmed = key.trim();
  if (!trimmed) throw new Error("API key is empty");
  await invoke("secrets_set", {
    service: KEYRING_SERVICE,
    account: getProvider(provider).keyringAccount,
    password: trimmed,
  });
}

async function deleteKey(account: string): Promise<void> {
  await invoke("secrets_delete", { service: KEYRING_SERVICE, account });
}

export async function clearKey(provider: ProviderId): Promise<void> {
  if (provider === "google" && (await getJarvis()).base)
    throw new Error(
      t("The shared Google key is managed by Jarvis. Replace it in Models & Providers."),
    );
  if (!providerSupportsKey(provider)) return;
  await deleteKey(getProvider(provider).keyringAccount);
}

export async function getAllKeys(): Promise<ProviderKeys> {
  const out = { ...EMPTY_PROVIDER_KEYS };
  const bridge = await getJarvis();
  if (bridge.base) out.google = await getKey("google");
  const need = PROVIDERS.filter((p) => providerSupportsKey(p.id));
  try {
    const results = await invoke<(string | null)[]>("secrets_get_all", {
      service: KEYRING_SERVICE,
      accounts: need.map((p) => p.keyringAccount),
    });
    need.forEach((p, i) => {
      const v = results[i];
      out[p.id] =
        p.id === "google" && bridge.base
          ? out.google
          : v && v.length > 0
            ? v
            : null;
    });
    return out;
  } catch {
    const entries = await Promise.all(
      need.map(async (p) => [p.id, await getKey(p.id)] as const),
    );
    for (const [id, v] of entries)
      out[id] = id === "google" && bridge.base ? out.google : v;
    return out;
  }
}

export function hasAnyKey(keys: ProviderKeys): boolean {
  return PROVIDERS.some((p) => providerSupportsKey(p.id) && !!keys[p.id]);
}

function compatKeyringAccount(endpointId: string): string {
  return `compat-${endpointId}-api-key`;
}

export async function getCustomEndpointKey(
  endpointId: string,
): Promise<string | null> {
  try {
    const v = await invoke<string | null>("secrets_get", {
      service: KEYRING_SERVICE,
      account: compatKeyringAccount(endpointId),
    });
    return v && v.length > 0 ? v : null;
  } catch {
    return null;
  }
}

export async function setCustomEndpointKey(
  endpointId: string,
  key: string,
): Promise<void> {
  const trimmed = key.trim();
  if (!trimmed) throw new Error("API key is empty");
  await invoke("secrets_set", {
    service: KEYRING_SERVICE,
    account: compatKeyringAccount(endpointId),
    password: trimmed,
  });
}

export async function clearCustomEndpointKey(
  endpointId: string,
): Promise<void> {
  await deleteKey(compatKeyringAccount(endpointId));
}

export async function getAllCustomEndpointKeys(
  endpoints: readonly CustomEndpoint[],
): Promise<CustomEndpointKeys> {
  if (endpoints.length === 0) return {};
  const out: CustomEndpointKeys = {};
  try {
    const accounts = endpoints.map((e) => compatKeyringAccount(e.id));
    const results = await invoke<(string | null)[]>("secrets_get_all", {
      service: KEYRING_SERVICE,
      accounts,
    });
    endpoints.forEach((e, i) => {
      const v = results[i];
      out[e.id] = v && v.length > 0 ? v : null;
    });
  } catch {
    for (const e of endpoints) {
      out[e.id] = await getCustomEndpointKey(e.id);
    }
  }
  return out;
}
