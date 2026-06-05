// Fireworks plugin module implements stream behavior.
import type { StreamFn } from "openclaw/plugin-sdk/agent-core";
import { streamSimple } from "openclaw/plugin-sdk/llm";
import type { ProviderWrapStreamFnContext } from "openclaw/plugin-sdk/plugin-entry";
import { normalizeProviderId } from "openclaw/plugin-sdk/provider-model-shared";
import { streamWithPayloadPatch } from "openclaw/plugin-sdk/provider-stream-shared";
import { isFireworksKimiModelId } from "./model-id.js";

function isFireworksProviderId(providerId: string): boolean {
  const normalized = normalizeProviderId(providerId);
  return normalized === "fireworks" || normalized === "fireworks-ai";
}

type PayloadFieldRead = { ok: true; value: unknown } | { ok: false };

function readPayloadField(record: Record<string, unknown>, key: string): PayloadFieldRead {
  try {
    return { ok: true, value: record[key] };
  } catch {
    return { ok: false };
  }
}

function forcePayloadField(record: Record<string, unknown>, key: string, value: unknown): boolean {
  try {
    Object.defineProperty(record, key, {
      configurable: true,
      enumerable: true,
      value,
      writable: true,
    });
    const next = readPayloadField(record, key);
    return next.ok && next.value === value;
  } catch {
    return false;
  }
}

function deletePayloadField(record: Record<string, unknown>, key: string): boolean {
  try {
    delete record[key];
    return !Object.hasOwn(record, key);
  } catch {
    return false;
  }
}

function removeFireworksPayloadField(payload: Record<string, unknown>, key: string): void {
  if (!deletePayloadField(payload, key)) {
    throw new Error(`Fireworks payload field could not be removed: ${key}`);
  }
}

export function createFireworksKimiThinkingDisabledWrapper(
  baseStreamFn: StreamFn | undefined,
): StreamFn {
  const underlying = baseStreamFn ?? streamSimple;
  return (model, context, options) =>
    streamWithPayloadPatch(underlying, model, context, options, (payloadObj) => {
      // Fireworks Kimi can emit chain-of-thought in visible `content` unless
      // the Anthropic-style thinking toggle is explicitly disabled.
      const disabledThinking = { type: "disabled" };
      if (!forcePayloadField(payloadObj, "thinking", disabledThinking)) {
        throw new Error("Fireworks thinking payload patch failed");
      }
      removeFireworksPayloadField(payloadObj, "reasoning");
      removeFireworksPayloadField(payloadObj, "reasoning_effort");
      removeFireworksPayloadField(payloadObj, "reasoningEffort");
    });
}

export function wrapFireworksProviderStream(
  ctx: ProviderWrapStreamFnContext,
): StreamFn | undefined {
  if (
    !isFireworksProviderId(ctx.provider) ||
    ctx.model?.api !== "openai-completions" ||
    !isFireworksKimiModelId(ctx.modelId)
  ) {
    return undefined;
  }
  return createFireworksKimiThinkingDisabledWrapper(ctx.streamFn);
}
