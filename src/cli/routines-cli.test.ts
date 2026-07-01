// Routines CLI tests cover durable-routine command parameter construction.
import { Command } from "commander";
import { afterEach, describe, expect, it, vi } from "vitest";
import { registerRoutinesCli } from "./routines-cli.js";

const mocks = vi.hoisted(() => {
  const defaultRuntime = {
    log: vi.fn(),
    error: vi.fn(),
    writeStdout: vi.fn(),
    writeJson: vi.fn(),
    exit: vi.fn((code: number) => {
      throw new Error(`__exit__:${code}`);
    }),
  };
  return {
    defaultRuntime,
    callGatewayFromCli: vi.fn(),
  };
});

const { callGatewayFromCli, defaultRuntime } = mocks;

vi.mock("./gateway-rpc.js", async () => {
  const actual = await vi.importActual<typeof import("./gateway-rpc.js")>("./gateway-rpc.js");
  return {
    ...actual,
    callGatewayFromCli: (method: string, opts: unknown, params?: unknown, extra?: unknown) =>
      mocks.callGatewayFromCli(method, opts, params, extra as number | undefined),
  };
});

vi.mock("../runtime.js", () => ({
  defaultRuntime: mocks.defaultRuntime,
}));

function buildProgram() {
  const program = new Command();
  program.exitOverride();
  registerRoutinesCli(program);
  return program;
}

function resetGatewayMock() {
  callGatewayFromCli.mockClear();
  callGatewayFromCli.mockImplementation(async (method: string) => {
    if (method === "cron.status") {
      return { enabled: true };
    }
    if (method === "routines.create") {
      return { created: true, routine: { id: "routine-1" } };
    }
    return {};
  });
  defaultRuntime.log.mockClear();
  defaultRuntime.error.mockClear();
  defaultRuntime.writeStdout.mockClear();
  defaultRuntime.writeJson.mockClear();
  defaultRuntime.exit.mockClear();
}

async function runRoutinesCommand(args: string[]): Promise<void> {
  resetGatewayMock();
  const program = buildProgram();
  await program.parseAsync(args, { from: "user" });
}

afterEach(() => {
  vi.useRealTimers();
});

describe("registerRoutinesCli", () => {
  it("defaults isolated message routines to last-channel announce delivery", async () => {
    await runRoutinesCommand([
      "routines",
      "create",
      "+1h",
      "check status",
      "--name",
      "Default delivery",
    ]);

    const createCall = callGatewayFromCli.mock.calls.find((call) => call[0] === "routines.create");
    const params = createCall?.[2] as {
      target?: { delivery?: { mode?: string; channel?: string } };
    };

    expect(params?.target?.delivery).toMatchObject({
      mode: "announce",
      channel: "last",
    });
  });

  it("rejects webhook delivery for main system-event routines", async () => {
    await expect(
      runRoutinesCommand([
        "routines",
        "create",
        "+1h",
        "--name",
        "Main webhook",
        "--system-event",
        "check status",
        "--webhook",
        "https://example.invalid/hook",
      ]),
    ).rejects.toThrow("__exit__:1");

    expect(callGatewayFromCli.mock.calls.some((call) => call[0] === "routines.create")).toBe(false);
    expect(defaultRuntime.error.mock.calls[0]?.[0]).toContain(
      "Delivery options require a non-main message routine.",
    );
  });

  it("rejects blank explicit routine ids before calling the Gateway", async () => {
    await expect(
      runRoutinesCommand([
        "routines",
        "create",
        "+1h",
        "check status",
        "--id",
        "   ",
        "--name",
        "Blank id",
      ]),
    ).rejects.toThrow("__exit__:1");

    expect(callGatewayFromCli.mock.calls.some((call) => call[0] === "routines.create")).toBe(false);
    expect(defaultRuntime.error.mock.calls[0]?.[0]).toContain("--id must not be blank");
  });
});
