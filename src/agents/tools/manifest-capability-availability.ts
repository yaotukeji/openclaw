import type { OpenClawConfig } from "../../config/types.openclaw.js";
import { getCurrentPluginMetadataSnapshot } from "../../plugins/current-plugin-metadata-snapshot.js";
import { isManifestPluginAvailableForControlPlane } from "../../plugins/manifest-contract-eligibility.js";
import type { PluginManifestRecord } from "../../plugins/manifest-registry.js";
import {
  hasNonEmptyManifestEnvCandidate,
  manifestConfigSignalPasses,
  manifestPluginSetupProviderEnvVars,
  manifestProviderBaseUrlGuardPasses,
} from "../../plugins/manifest-tool-availability.js";
import { resolvePluginMetadataSnapshot } from "../../plugins/plugin-metadata-snapshot.js";
import type { PluginMetadataSnapshot } from "../../plugins/plugin-metadata-snapshot.types.js";
import { getActivePluginRegistryWorkspaceDirFromState } from "../../plugins/runtime-state.js";
import { listProfilesForProvider } from "../auth-profiles/profile-list.js";
import type { AuthProfileStore } from "../auth-profiles/types.js";

export type CapabilityContractKey =
  | "imageGenerationProviders"
  | "videoGenerationProviders"
  | "musicGenerationProviders"
  | "mediaUnderstandingProviders";

type CapabilityProviderMetadataKey =
  | "imageGenerationProviderMetadata"
  | "videoGenerationProviderMetadata"
  | "musicGenerationProviderMetadata";
type CapabilityConfigSignal = Parameters<typeof manifestConfigSignalPasses>[0]["signal"];
type CapabilityProviderBaseUrl = Parameters<typeof manifestProviderBaseUrlGuardPasses>[0]["guard"];
type CapabilityAuthSignal = {
  provider: string;
  providerBaseUrl?: CapabilityProviderBaseUrl;
};
type CapabilityAuthSignalsResult = {
  signals: CapabilityAuthSignal[];
  invalid: boolean;
};
type ReadValueResult = { ok: true; value: unknown } | { ok: false };
type ArrayCopyResult = { ok: true; entries: unknown[] } | { ok: false; entries: [] };

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readRecordValueResult(record: unknown, key: string): ReadValueResult {
  if (!isRecord(record)) {
    return { ok: true, value: undefined };
  }
  try {
    return { ok: true, value: record[key] };
  } catch {
    return { ok: false };
  }
}

function readRecordValue(record: unknown, key: string): unknown {
  const result = readRecordValueResult(record, key);
  return result.ok ? result.value : undefined;
}

function copyArrayEntries(value: unknown): unknown[] {
  if (!Array.isArray(value)) {
    return [];
  }
  let length = 0;
  try {
    length = value.length;
  } catch {
    return [];
  }
  const entries: unknown[] = [];
  for (let index = 0; index < length; index += 1) {
    try {
      entries.push(value[index]);
    } catch {
      // Skip unreadable manifest metadata entries; later providers can still prove availability.
    }
  }
  return entries;
}

function copyArrayEntriesResult(value: unknown): ArrayCopyResult {
  if (!Array.isArray(value)) {
    return { ok: true, entries: [] };
  }
  let length = 0;
  try {
    length = value.length;
  } catch {
    return { ok: false, entries: [] };
  }
  const entries: unknown[] = [];
  for (let index = 0; index < length; index += 1) {
    try {
      entries.push(value[index]);
    } catch {
      return { ok: false, entries: [] };
    }
  }
  return { ok: true, entries };
}

function copyStringArrayEntries(value: unknown): string[] {
  return copyArrayEntries(value).filter((entry): entry is string => typeof entry === "string");
}

type CapabilityMetadataSnapshot = Pick<PluginMetadataSnapshot, "index" | "plugins">;

function metadataKeyForCapabilityContract(
  key: CapabilityContractKey,
): CapabilityProviderMetadataKey | undefined {
  switch (key) {
    case "imageGenerationProviders":
      return "imageGenerationProviderMetadata";
    case "videoGenerationProviders":
      return "videoGenerationProviderMetadata";
    case "musicGenerationProviders":
      return "musicGenerationProviderMetadata";
    case "mediaUnderstandingProviders":
      return undefined;
  }
  return undefined;
}

function listCapabilityAuthSignals(params: {
  plugin: PluginManifestRecord;
  key: CapabilityContractKey;
  providerId: string;
}): CapabilityAuthSignalsResult {
  const metadata = readCapabilityProviderMetadata(params.plugin, params.key, params.providerId);
  const rawAuthSignals = readRecordValueResult(metadata, "authSignals");
  if (!rawAuthSignals.ok) {
    return { signals: [], invalid: true };
  }
  const hasAuthSignals = rawAuthSignals.value !== undefined;
  if (hasAuthSignals && !Array.isArray(rawAuthSignals.value)) {
    return { signals: [], invalid: true };
  }
  const authSignalEntries = copyArrayEntriesResult(rawAuthSignals.value);
  if (!authSignalEntries.ok) {
    return { signals: [], invalid: true };
  }
  let invalid = false;
  const authSignals = authSignalEntries.entries.flatMap((signal) => {
    if (!isRecord(signal)) {
      invalid = true;
      return [];
    }
    const providerResult = readRecordValueResult(signal, "provider");
    const providerBaseUrlResult = readRecordValueResult(signal, "providerBaseUrl");
    if (!providerResult.ok || !providerBaseUrlResult.ok) {
      invalid = true;
      return [];
    }
    const provider = providerResult.value;
    if (typeof provider !== "string") {
      invalid = true;
      return [];
    }
    const providerBaseUrl = providerBaseUrlResult.value as CapabilityProviderBaseUrl | undefined;
    return [{ provider, ...(providerBaseUrl ? { providerBaseUrl } : {}) }];
  });
  if (hasAuthSignals) {
    return { signals: authSignals, invalid: invalid || authSignals.length === 0 };
  }
  return {
    signals: [
      params.providerId,
      ...copyStringArrayEntries(readRecordValue(metadata, "aliases")),
      ...copyStringArrayEntries(readRecordValue(metadata, "authProviders")),
    ].map((provider) => ({ provider })),
    invalid,
  };
}

function readCapabilityProviderMetadata(
  plugin: PluginManifestRecord,
  key: CapabilityContractKey,
  providerId: string,
): Record<string, unknown> | undefined {
  const metadataKey = metadataKeyForCapabilityContract(key);
  const metadata = metadataKey
    ? readRecordValue(readRecordValue(plugin, metadataKey), providerId)
    : undefined;
  return isRecord(metadata) ? metadata : undefined;
}

function listCapabilityProviderIds(
  plugin: PluginManifestRecord,
  key: CapabilityContractKey,
): string[] {
  return copyStringArrayEntries(readRecordValue(readRecordValue(plugin, "contracts"), key));
}

function listCapabilityConfigSignals(metadata: unknown): CapabilityConfigSignal[] {
  return copyArrayEntries(readRecordValue(metadata, "configSignals")).filter(
    (signal): signal is CapabilityConfigSignal => isRecord(signal),
  );
}

function isPluginAvailableForCapability(params: {
  snapshot: CapabilityMetadataSnapshot;
  plugin: PluginManifestRecord;
  config?: OpenClawConfig;
}): boolean {
  return isManifestPluginAvailableForControlPlane({
    snapshot: params.snapshot,
    plugin: params.plugin,
    config: params.config,
  });
}

function hasAvailableCapabilityPlugin(
  params: {
    snapshot: CapabilityMetadataSnapshot;
    config?: OpenClawConfig;
  },
  accepts: (plugin: PluginManifestRecord) => boolean,
): boolean {
  if (params.config?.plugins?.enabled === false) {
    return false;
  }
  for (const plugin of params.snapshot.plugins) {
    if (
      !isPluginAvailableForCapability({
        snapshot: params.snapshot,
        plugin,
        config: params.config,
      })
    ) {
      continue;
    }
    if (accepts(plugin)) {
      return true;
    }
  }
  return false;
}

function capabilityConfigSignalPasses(params: {
  config?: OpenClawConfig;
  env: NodeJS.ProcessEnv;
  signal: CapabilityConfigSignal;
}): boolean {
  try {
    return manifestConfigSignalPasses(params);
  } catch {
    return false;
  }
}

function providerBaseUrlGuardPasses(params: {
  config?: OpenClawConfig;
  guard: CapabilityProviderBaseUrl;
}): boolean {
  try {
    return manifestProviderBaseUrlGuardPasses(params);
  } catch {
    return false;
  }
}

function hasManifestProviderEnvSignal(
  env: NodeJS.ProcessEnv,
  plugin: PluginManifestRecord,
  providerId: string,
): boolean {
  try {
    return hasNonEmptyManifestEnvCandidate(
      env,
      manifestPluginSetupProviderEnvVars(plugin, providerId),
    );
  } catch {
    return false;
  }
}

function hasConfiguredCapabilityProviderSignal(params: {
  plugin: PluginManifestRecord;
  key: CapabilityContractKey;
  providerId: string;
  config?: OpenClawConfig;
  authStore?: AuthProfileStore;
}): boolean {
  const metadata = readCapabilityProviderMetadata(params.plugin, params.key, params.providerId);
  if (
    listCapabilityConfigSignals(metadata).some((signal) =>
      capabilityConfigSignalPasses({
        config: params.config,
        env: process.env,
        signal,
      }),
    )
  ) {
    return true;
  }
  const authSignals = listCapabilityAuthSignals({
    plugin: params.plugin,
    key: params.key,
    providerId: params.providerId,
  });
  if (authSignals.invalid) {
    return false;
  }
  for (const signal of authSignals.signals) {
    if (
      !providerBaseUrlGuardPasses({
        config: params.config,
        guard: signal.providerBaseUrl,
      })
    ) {
      continue;
    }
    if (params.authStore && listProfilesForProvider(params.authStore, signal.provider).length > 0) {
      return true;
    }
    if (hasManifestProviderEnvSignal(process.env, params.plugin, signal.provider)) {
      return true;
    }
  }
  return false;
}

export function getCurrentCapabilityMetadataSnapshot(params: {
  config?: OpenClawConfig;
  workspaceDir?: string;
}): PluginMetadataSnapshot | undefined {
  const workspaceDir = params.workspaceDir ?? getActivePluginRegistryWorkspaceDirFromState();
  return getCurrentPluginMetadataSnapshot({
    config: params.config,
    ...(workspaceDir ? { workspaceDir } : {}),
  });
}

export function loadCapabilityMetadataSnapshot(params: {
  config?: OpenClawConfig;
  workspaceDir?: string;
  env?: NodeJS.ProcessEnv;
}): Pick<PluginMetadataSnapshot, "index" | "plugins"> {
  const workspaceDir = params.workspaceDir ?? getActivePluginRegistryWorkspaceDirFromState();
  return resolvePluginMetadataSnapshot({
    config: params.config ?? {},
    env: params.env ?? process.env,
    ...(workspaceDir ? { workspaceDir } : {}),
  });
}

export function hasSnapshotCapabilityAvailability(params: {
  snapshot: CapabilityMetadataSnapshot;
  key: CapabilityContractKey;
  config?: OpenClawConfig;
  authStore?: AuthProfileStore;
}): boolean {
  return hasAvailableCapabilityPlugin(params, (plugin) =>
    listCapabilityProviderIds(plugin, params.key).some((providerId) =>
      hasConfiguredCapabilityProviderSignal({
        plugin,
        key: params.key,
        providerId,
        config: params.config,
        authStore: params.authStore,
      }),
    ),
  );
}

export function hasSnapshotProviderEnvAvailability(params: {
  snapshot: CapabilityMetadataSnapshot;
  providerId: string;
  config?: OpenClawConfig;
}): boolean {
  return hasAvailableCapabilityPlugin(params, (plugin) =>
    hasManifestProviderEnvSignal(process.env, plugin, params.providerId),
  );
}

export function hasSnapshotCapabilityProviderAvailability(params: {
  snapshot: CapabilityMetadataSnapshot;
  key: CapabilityContractKey;
  providerId: string;
  config?: OpenClawConfig;
  authStore?: AuthProfileStore;
}): boolean {
  return hasAvailableCapabilityPlugin(params, (plugin) => {
    if (!listCapabilityProviderIds(plugin, params.key).includes(params.providerId)) {
      return false;
    }
    return hasConfiguredCapabilityProviderSignal({
      plugin,
      key: params.key,
      providerId: params.providerId,
      config: params.config,
      authStore: params.authStore,
    });
  });
}
