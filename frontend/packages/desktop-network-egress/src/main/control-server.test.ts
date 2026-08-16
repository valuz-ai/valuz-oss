import { afterEach, describe, expect, it, vi } from "vitest";
import { EgressControlServer } from "./control-server";

const controls: EgressControlServer[] = [];

afterEach(async () => {
  await Promise.all(controls.splice(0).map((control) => control.stop()));
});

describe("EgressControlServer", () => {
  it("requires the in-memory bootstrap token and validates registration shape", async () => {
    const register = vi.fn(() => ({
      kind: "model_ingress" as const,
      baseUrl: "http://127.0.0.1:9000/random/v1",
      clientId: "runtime_client_1234",
      expiresAt: 200,
      supportsWebSocket: true,
    }));
    const control = new EgressControlServer({
      mode: "auto",
      registerModelIngress: register,
      registerForwardProxy: vi.fn(() => {
        throw new Error("unused");
      }),
      revokeClient: vi.fn(),
      renewClients: vi.fn(),
      now: () => 100,
    });
    controls.push(control);
    await control.start();
    const bootstrap = control.bootstrap();

    await expect(
      fetch(`${bootstrap.controlEndpoint}/v1/clients/model-ingress`, {
        method: "POST",
        body: "{}",
      }),
    ).resolves.toMatchObject({ status: 401 });

    const response = await fetch(
      `${bootstrap.controlEndpoint}/v1/clients/model-ingress`,
      {
        method: "POST",
        headers: {
          authorization: `Bearer ${bootstrap.bootstrapToken}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          clientId: "runtime_client_1234",
          runtime: "claude",
          upstreamBaseUrl: "https://api.example/v1",
          supportsWebSocket: true,
        }),
      },
    );
    expect(response.status).toBe(201);
    expect(await response.json()).toMatchObject({ kind: "model_ingress" });
    expect(register).toHaveBeenCalledWith({
      clientId: "runtime_client_1234",
      runtime: "claude",
      upstreamBaseUrl: "https://api.example/v1",
      supportsWebSocket: true,
    });
  });

  it("revokes a runtime capability without returning any secret", async () => {
    const revoke = vi.fn();
    const control = new EgressControlServer({
      mode: "direct",
      registerModelIngress: vi.fn(() => {
        throw new Error("unused");
      }),
      registerForwardProxy: vi.fn(() => {
        throw new Error("unused");
      }),
      revokeClient: revoke,
      renewClients: vi.fn(),
    });
    controls.push(control);
    await control.start();
    const bootstrap = control.bootstrap();
    const response = await fetch(
      `${bootstrap.controlEndpoint}/v1/clients/runtime_client_1234`,
      {
        method: "DELETE",
        headers: { authorization: `Bearer ${bootstrap.bootstrapToken}` },
      },
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ revoked: true });
    expect(revoke).toHaveBeenCalledWith("runtime_client_1234");
  });

  it("renews the bootstrap and active client leases without rotating their secrets", async () => {
    let now = 100;
    const renew = vi.fn();
    const control = new EgressControlServer({
      mode: "auto",
      registerModelIngress: vi.fn(() => {
        throw new Error("unused");
      }),
      registerForwardProxy: vi.fn(() => {
        throw new Error("unused");
      }),
      revokeClient: vi.fn(),
      renewClients: renew,
      now: () => now,
      ttlMs: 1_000,
    });
    controls.push(control);
    await control.start();
    const original = control.bootstrap();
    now = 500;

    const response = await fetch(`${original.controlEndpoint}/v1/lease/renew`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${original.bootstrapToken}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({ clientIds: ["runtime_client_1234"] }),
    });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ expiresAt: 1_500 });
    expect(renew).toHaveBeenCalledWith(["runtime_client_1234"], 1_500);
    expect(control.bootstrap()).toMatchObject({
      bootstrapToken: original.bootstrapToken,
      expiresAt: 1_500,
    });
  });
});
