/** @vitest-environment jsdom */
import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ProviderDetail } from "../api/providers-api";
import { useComposerProviders } from "./use-composer-providers";

const provider = (
  overrides: Partial<ProviderDetail> & Pick<ProviderDetail, "id" | "name">,
): ProviderDetail => ({
  provider_kind: "anthropic",
  source: "managed",
  enabled: true,
  is_default: false,
  deletable: true,
  default_model: null,
  test_status: "never",
  credential_source: "secret_ref",
  auth_type: "api_key",
  base_url: null,
  model_options: [],
  model_labels: {},
  unavailable_reason: null,
  supports_custom_base_url: false,
  supports_connection_test: true,
  protocol: null,
  effective_protocol: "anthropic",
  compatible_protocols: ["anthropic"],
  ...overrides,
});

describe("useComposerProviders", () => {
  it("flattens enabled providers into one entry per (provider, model)", () => {
    const providers = [
      provider({
        id: "ch-anthropic",
        name: "Anthropic",
        model_options: ["claude-sonnet-4-6", "claude-opus-4-7"],
      }),
      provider({
        id: "ch-openai",
        name: "OpenAI",
        model_options: ["gpt-4o"],
      }),
    ];

    const { result } = renderHook(() => useComposerProviders(providers));
    expect(result.current.map((m) => `${m.providerId}:${m.modelId}`)).toEqual([
      "ch-anthropic:claude-sonnet-4-6",
      "ch-anthropic:claude-opus-4-7",
      "ch-openai:gpt-4o",
    ]);
  });

  it("filters out disabled providers", () => {
    const providers = [
      provider({
        id: "ch-on",
        name: "On",
        model_options: ["m1"],
      }),
      provider({
        id: "ch-off",
        name: "Off",
        enabled: false,
        model_options: ["m2"],
      }),
    ];

    const { result } = renderHook(() => useComposerProviders(providers));
    expect(result.current.map((m) => m.providerId)).toEqual(["ch-on"]);
  });

  it("falls back to default_model when model_options is empty", () => {
    const providers = [
      provider({
        id: "ch-bare",
        name: "Bare",
        default_model: "fallback-id",
      }),
    ];

    const { result } = renderHook(() => useComposerProviders(providers));
    expect(result.current).toEqual([
      {
        providerId: "ch-bare",
        providerName: "Bare",
        modelId: "fallback-id",
        isDefault: false,
        source: "managed",
      },
    ]);
  });

  it("for runtimeFilter=deepagents shows every credentialed provider except OAuth-subscription ones", () => {
    const providers = [
      provider({
        id: "ch-anthropic",
        name: "Anthropic",
        provider_kind: "anthropic",
        model_options: ["claude-sonnet-4-6"],
      }),
      provider({
        id: "ch-openai",
        name: "OpenAI",
        provider_kind: "openai",
        model_options: ["gpt-4o"],
      }),
      provider({
        id: "ch-claude-subscription",
        name: "Claude (订阅)",
        provider_kind: "claude-subscription",
        credential_source: "none",
        auth_type: "oauth",
        model_options: ["claude-sonnet-4-6"],
      }),
      provider({
        id: "ch-codex-subscription",
        name: "Codex (订阅)",
        provider_kind: "codex-subscription",
        credential_source: "none",
        auth_type: "oauth",
        model_options: ["gpt-5-codex"],
      }),
    ];

    const { result } = renderHook(() =>
      useComposerProviders(providers, "deepagents"),
    );
    expect(result.current.map((m) => m.providerId)).toEqual([
      "ch-anthropic",
      "ch-openai",
    ]);
  });

  it("for runtimeFilter=claude_agent matches by effective protocol", () => {
    // claude_agent's supported_protocols == ("anthropic",), so the
    // picker should surface every anthropic-protocol provider even
    // when its ``runtime_provider`` column has drifted (e.g. a
    // non-``compatible`` provider_kind switched to anthropic
    // protocol — providers/service.py only re-syncs runtime_provider
    // for ``compatible`` rows).
    const providers = [
      // Built-in: provider_kind anthropic, protocol blank → derived
      // anthropic. SHOULD appear.
      provider({
        id: "ch-anthropic",
        name: "Anthropic",
        provider_kind: "anthropic",
        protocol: null,
        model_options: ["claude-sonnet-4-6"],
      }),
      // Built-in subscription: provider_kind claude-subscription,
      // protocol blank → derived anthropic. SHOULD appear.
      provider({
        id: "ch-claude-subscription",
        name: "Claude (订阅)",
        provider_kind: "claude-subscription",
        protocol: null,
        credential_source: "none",
        auth_type: "oauth",
        model_options: ["claude-sonnet-4-6"],
      }),
      // User provider: deepseek kind, anthropic protocol explicit.
      // SHOULD appear under the protocol-based filter.
      provider({
        id: "ch-deepseek-anthropic",
        name: "DeepSeek (anthropic)",
        provider_kind: "deepseek",
        protocol: "anthropic",
        credential_source: "secret_ref",
        model_options: ["deepseek-v4"],
      }),
      // DeepSeek (dual-protocol built-in): backend marks it as both
      // anthropic + openai capable via ``compatible_protocols``. SHOULD
      // appear here — runtime drives base_url to ``<base>/anthropic``
      // at resolve time.
      provider({
        id: "ch-deepseek-dual",
        name: "DeepSeek",
        provider_kind: "deepseek",
        protocol: null,
        compatible_protocols: ["anthropic", "openai-completion"],
        credential_source: "secret_ref",
        model_options: ["deepseek-v4-flash"],
      }),
      // OpenAI-protocol provider — should NOT appear.
      provider({
        id: "ch-openai",
        name: "OpenAI",
        provider_kind: "openai",
        protocol: null,
        compatible_protocols: ["openai-completion"],
        model_options: ["gpt-4o"],
      }),
      provider({
        id: "ch-compat-openai",
        name: "Custom (OpenAI)",
        provider_kind: "compatible",
        protocol: "openai-completion",
        compatible_protocols: ["openai-completion"],
        model_options: ["custom-gpt"],
      }),
      // Codex subscription — speaks openai-response (kernel V5+bba3014
      // 4-value enum), must be excluded from the deepagents picker
      // because its credentials live in the codex CLI's keychain.
      provider({
        id: "ch-codex",
        name: "Codex (订阅)",
        provider_kind: "codex-subscription",
        protocol: null,
        compatible_protocols: ["openai-response"],
        credential_source: "none",
        auth_type: "oauth",
        model_options: ["gpt-5-codex"],
      }),
    ];

    const { result } = renderHook(() =>
      useComposerProviders(providers, "claude_agent"),
    );
    expect(result.current.map((m) => m.providerId)).toEqual([
      "ch-anthropic",
      "ch-claude-subscription",
      "ch-deepseek-anthropic",
      "ch-deepseek-dual",
    ]);
  });

  it("for runtimeFilter=codex only shows codex-subscription (not claude-subscription)", () => {
    const providers = [
      provider({
        id: "ch-codex-subscription",
        name: "Codex (订阅)",
        provider_kind: "codex-subscription",
        credential_source: "none",
        auth_type: "oauth",
        model_options: ["gpt-5-codex"],
      }),
      // Claude subscription — codex CLI can't authenticate to Anthropic's
      // API (reads its own keychain via codex /login), so the picker
      // intentionally excludes it.
      provider({
        id: "ch-claude-subscription",
        name: "Claude (订阅)",
        provider_kind: "claude-subscription",
        credential_source: "none",
        auth_type: "oauth",
        model_options: ["claude-sonnet-4-6"],
      }),
      // OpenAI api_key provider — codex CLI only speaks the Responses
      // API, not arbitrary openai-compatible endpoints, so the picker
      // intentionally hides it.
      provider({
        id: "ch-openai",
        name: "OpenAI",
        provider_kind: "openai",
        model_options: ["gpt-4o"],
      }),
    ];

    const { result } = renderHook(() =>
      useComposerProviders(providers, "codex"),
    );
    expect(result.current.map((m) => m.providerId)).toEqual([
      "ch-codex-subscription",
    ]);
  });

  it("for runtimeFilter=codex also surfaces system providers compatible with openai-response", () => {
    // Cloud-backend gateway → overlay registers an openai-response system
    // descriptor (provider_kind="system", compatible_protocols=["openai-response"]).
    // The codex picker must surface it alongside the OAuth subscription.
    const providers = [
      provider({
        id: "ch-codex-subscription",
        name: "Codex (订阅)",
        provider_kind: "codex-subscription",
        credential_source: "none",
        auth_type: "oauth",
        compatible_protocols: ["openai-response"],
        model_options: ["gpt-5-codex"],
      }),
      provider({
        id: "valuz-channel-codex",
        name: "Valuz 系统模型",
        provider_kind: "system",
        source: "system",
        credential_source: "system_managed",
        auth_type: "oauth",
        compatible_protocols: ["openai-response"],
        model_options: ["gpt-5.4-nano"],
      }),
      // Anthropic-only system provider — must NOT leak into the codex card.
      provider({
        id: "valuz-channel",
        name: "Valuz 系统模型",
        provider_kind: "system",
        source: "system",
        credential_source: "system_managed",
        auth_type: "oauth",
        compatible_protocols: ["anthropic"],
        model_options: ["sys-reportify-pro"],
      }),
    ];

    const { result } = renderHook(() =>
      useComposerProviders(providers, "codex"),
    );
    expect(result.current.map((m) => m.providerId)).toEqual([
      "ch-codex-subscription",
      "valuz-channel-codex",
    ]);
  });

  it("for runtimeFilter=deepagents excludes system providers that only speak openai-response", () => {
    // Mirror of the codex case: the openai-response system provider must
    // NOT appear under the Deep Agents card (Valuz Agent SDK doesn't speak
    // the Responses API).
    const providers = [
      // Anthropic-capable system provider — SHOULD appear.
      provider({
        id: "valuz-channel",
        name: "Valuz 系统模型",
        provider_kind: "system",
        source: "system",
        credential_source: "system_managed",
        auth_type: "oauth",
        compatible_protocols: ["anthropic"],
        model_options: ["sys-reportify-pro"],
      }),
      // openai-response-only system provider — should be filtered out.
      provider({
        id: "valuz-channel-codex",
        name: "Valuz 系统模型",
        provider_kind: "system",
        source: "system",
        credential_source: "system_managed",
        auth_type: "oauth",
        compatible_protocols: ["openai-response"],
        model_options: ["gpt-5.4-nano"],
      }),
    ];

    const { result } = renderHook(() =>
      useComposerProviders(providers, "deepagents"),
    );
    expect(result.current.map((m) => m.providerId)).toEqual(["valuz-channel"]);
  });

  it("ignores runtimeFilter=undefined (backwards compat)", () => {
    const providers = [
      provider({
        id: "ch-anthropic",
        name: "Anthropic",
        model_options: ["m1"],
      }),
      provider({
        id: "ch-openai",
        name: "OpenAI",
        model_options: ["m2"],
      }),
    ];

    const { result } = renderHook(() =>
      useComposerProviders(providers, undefined),
    );
    expect(result.current.map((m) => m.providerId)).toEqual([
      "ch-anthropic",
      "ch-openai",
    ]);
  });

  it("hides api_key providers with no credentials configured", () => {
    const providers = [
      // configured — should appear
      provider({
        id: "ch-anthropic-configured",
        name: "Anthropic",
        credential_source: "secret_ref",
        model_options: ["m1"],
      }),
      // empty — should be filtered out
      provider({
        id: "ch-anthropic-blank",
        name: "Anthropic blank",
        credential_source: "none",
        auth_type: "api_key",
        model_options: ["m2"],
      }),
    ];

    const { result } = renderHook(() => useComposerProviders(providers));
    expect(result.current.map((m) => m.providerId)).toEqual([
      "ch-anthropic-configured",
    ]);
  });

  it("keeps oauth providers even when credential_source is none (CLI manages keychain)", () => {
    const providers = [
      provider({
        id: "ch-claude-subscription",
        name: "Claude (订阅)",
        credential_source: "none",
        auth_type: "oauth",
        model_options: ["claude-sonnet-4-6"],
      }),
    ];

    const { result } = renderHook(() => useComposerProviders(providers));
    expect(result.current.map((m) => m.providerId)).toEqual([
      "ch-claude-subscription",
    ]);
  });

  it("keeps account_connection providers (Reportify-style OAuth)", () => {
    const providers = [
      provider({
        id: "ch-reportify",
        name: "Valuz",
        credential_source: "account_connection",
        auth_type: "api_key",
        model_options: ["reportify-lite"],
      }),
    ];

    const { result } = renderHook(() => useComposerProviders(providers));
    expect(result.current.map((m) => m.providerId)).toEqual(["ch-reportify"]);
  });
});
