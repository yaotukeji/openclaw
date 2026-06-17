// Browser tests cover pw tools core.snapshot.navigate guard plugin behavior.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SsrFBlockedError } from "../infra/net/ssrf.js";
import "../test-support/browser-security.mock.js";
import { InvalidBrowserNavigationUrlError } from "./navigation-guard.js";
import {
  getPwToolsCoreSessionMocks,
  installPwToolsCoreTestHooks,
  setPwToolsCoreCurrentPage,
} from "./pw-tools-core.test-harness.js";

installPwToolsCoreTestHooks();
const mod = await import("./pw-tools-core.snapshot.js");

const PROXY_ENV_KEYS = [
  "HTTP_PROXY",
  "HTTPS_PROXY",
  "ALL_PROXY",
  "http_proxy",
  "https_proxy",
  "all_proxy",
] as const;

describe("pw-tools-core.snapshot navigate guard", () => {
  beforeEach(() => {
    for (const key of PROXY_ENV_KEYS) {
      vi.stubEnv(key, "");
    }
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  function createNavigateDownloadPage(params: {
    download?: {
      url?: () => string;
      suggestedFilename?: () => string;
      path?: () => Promise<string>;
    };
    finalUrl?: string;
  }) {
    const downloadHandlers = new Set<(download: unknown) => void>();
    const page = {
      goto: vi.fn(async () => {
        if (params.download) {
          for (const handler of downloadHandlers) {
            handler(params.download);
          }
        }
        throw new Error("page.goto: net::ERR_ABORTED");
      }),
      on: vi.fn((event: string, handler: (download: unknown) => void) => {
        if (event === "download") {
          downloadHandlers.add(handler);
        }
      }),
      off: vi.fn((event: string, handler: (download: unknown) => void) => {
        if (event === "download") {
          downloadHandlers.delete(handler);
        }
      }),
      url: vi.fn(() => params.finalUrl ?? "about:blank"),
    };
    return { page, downloadHandlers };
  }

  it("blocks unsupported non-network URLs before page lookup", async () => {
    const goto = vi.fn(async () => {});
    setPwToolsCoreCurrentPage({
      goto,
      url: vi.fn(() => "about:blank"),
    });

    await expect(
      mod.navigateViaPlaywright({
        cdpUrl: "http://127.0.0.1:18792",
        url: "file:///etc/passwd",
      }),
    ).rejects.toBeInstanceOf(InvalidBrowserNavigationUrlError);

    expect(getPwToolsCoreSessionMocks().getPageForTargetId).not.toHaveBeenCalled();
    expect(goto).not.toHaveBeenCalled();
  });

  it("navigates valid network URLs with clamped timeout", async () => {
    const goto = vi.fn(async () => {});
    const page = {
      goto,
      url: vi.fn(() => "https://example.com"),
    };
    setPwToolsCoreCurrentPage(page);

    const result = await mod.navigateViaPlaywright({
      cdpUrl: "http://127.0.0.1:18792",
      url: "https://example.com",
      timeoutMs: 10,
      ssrfPolicy: { allowPrivateNetwork: true },
    });

    expect(goto).toHaveBeenCalledWith("https://example.com", { timeout: 1000 });
    expect(getPwToolsCoreSessionMocks().gotoPageWithNavigationGuard).toHaveBeenCalledWith({
      cdpUrl: "http://127.0.0.1:18792",
      page,
      ssrfPolicy: { allowPrivateNetwork: true },
      targetId: undefined,
      timeoutMs: 1000,
      url: "https://example.com",
    });
    expect(getPwToolsCoreSessionMocks().assertPageNavigationCompletedSafely).toHaveBeenCalledWith({
      cdpUrl: "http://127.0.0.1:18792",
      page,
      response: null,
      ssrfPolicy: { allowPrivateNetwork: true },
      targetId: undefined,
    });
    expect(result.url).toBe("https://example.com");
  });

  it("returns managed download metadata when a direct attachment navigation aborts", async () => {
    const { page } = createNavigateDownloadPage({
      download: {
        url: () => "https://example.com/report.csv",
        suggestedFilename: () => "report.csv",
        path: async () => "/tmp/openclaw/downloads/report.csv",
      },
    });
    setPwToolsCoreCurrentPage(page);

    const result = await mod.navigateViaPlaywright({
      cdpUrl: "http://127.0.0.1:18792",
      targetId: "tab-1",
      url: "https://example.com/report.csv",
      ssrfPolicy: { allowPrivateNetwork: true },
    });

    expect(result).toEqual({
      url: "https://example.com/report.csv",
      download: {
        url: "https://example.com/report.csv",
        suggestedFilename: "report.csv",
        path: "/tmp/openclaw/downloads/report.csv",
      },
    });
    expect(getPwToolsCoreSessionMocks().assertPageNavigationCompletedSafely).not.toHaveBeenCalled();
    expect(getPwToolsCoreSessionMocks().closeBlockedNavigationTarget).not.toHaveBeenCalled();
  });

  it("rejects a direct attachment download when the captured download URL violates policy", async () => {
    const saveAs = vi.fn(async () => {});
    const { page } = createNavigateDownloadPage({
      download: {
        url: () => "http://127.0.0.1:18080/private.csv",
        suggestedFilename: () => "private.csv",
        path: async () => {
          await saveAs();
          return "/tmp/openclaw/downloads/private.csv";
        },
      },
    });
    setPwToolsCoreCurrentPage(page);

    await expect(
      mod.navigateViaPlaywright({
        cdpUrl: "http://127.0.0.1:18792",
        targetId: "tab-1",
        url: "https://example.com/private.csv",
      }),
    ).rejects.toBeInstanceOf(SsrFBlockedError);

    expect(saveAs).not.toHaveBeenCalled();
    expect(getPwToolsCoreSessionMocks().closeBlockedNavigationTarget).not.toHaveBeenCalled();
  });

  it("does not capture direct attachment downloads while an explicit download waiter owns them", async () => {
    const { page } = createNavigateDownloadPage({
      download: {
        url: () => "https://example.com/report.csv",
        suggestedFilename: () => "report.csv",
        path: async () => "/tmp/openclaw/downloads/report.csv",
      },
    });
    setPwToolsCoreCurrentPage(page);
    getPwToolsCoreSessionMocks().ensurePageState(page).downloadWaiterDepth = 1;

    await expect(
      mod.navigateViaPlaywright({
        cdpUrl: "http://127.0.0.1:18792",
        targetId: "tab-1",
        url: "https://example.com/report.csv",
      }),
    ).rejects.toThrow("net::ERR_ABORTED");

    expect(getPwToolsCoreSessionMocks().createManagedPageDownloadWaiter).toHaveReturnedWith(null);
    expect(getPwToolsCoreSessionMocks().assertPageNavigationCompletedSafely).not.toHaveBeenCalled();
  });

  it("reconnects and retries once when navigation detaches frame", async () => {
    const goto = vi
      .fn<(...args: unknown[]) => Promise<void>>()
      .mockRejectedValueOnce(new Error("page.goto: Frame has been detached"))
      .mockResolvedValueOnce(undefined);
    setPwToolsCoreCurrentPage({
      goto,
      url: vi.fn(() => "https://example.com/recovered"),
    });

    const result = await mod.navigateViaPlaywright({
      cdpUrl: "http://127.0.0.1:18792",
      targetId: "tab-1",
      url: "https://example.com/recovered",
      ssrfPolicy: { allowPrivateNetwork: true },
    });

    expect(getPwToolsCoreSessionMocks().getPageForTargetId).toHaveBeenCalledTimes(2);
    expect(getPwToolsCoreSessionMocks().forceDisconnectPlaywrightForTarget).toHaveBeenCalledTimes(
      1,
    );
    expect(getPwToolsCoreSessionMocks().forceDisconnectPlaywrightForTarget).toHaveBeenCalledWith({
      cdpUrl: "http://127.0.0.1:18792",
      targetId: "tab-1",
      ssrfPolicy: { allowPrivateNetwork: true },
      reason: "retry navigate after detached frame",
    });
    expect(getPwToolsCoreSessionMocks().gotoPageWithNavigationGuard).toHaveBeenCalledTimes(2);
    expect(result.url).toBe("https://example.com/recovered");
  });

  it("blocks private intermediate redirect hops during navigation", async () => {
    const goto = vi.fn(async () => ({
      request: () => ({
        url: () => "https://93.184.216.34/final",
        redirectedFrom: () => ({
          url: () => "http://127.0.0.1:18080/internal-hop",
          redirectedFrom: () => ({
            url: () => "https://93.184.216.34/start",
            redirectedFrom: () => null,
          }),
        }),
      }),
    }));
    const page = {
      goto,
      url: vi.fn(() => "https://93.184.216.34/final"),
    };
    setPwToolsCoreCurrentPage(page);
    getPwToolsCoreSessionMocks().assertPageNavigationCompletedSafely.mockRejectedValueOnce(
      new SsrFBlockedError("Blocked hostname or private/internal/special-use IP address"),
    );

    await expect(
      mod.navigateViaPlaywright({
        cdpUrl: "http://127.0.0.1:18792",
        url: "https://93.184.216.34/start",
      }),
    ).rejects.toBeInstanceOf(SsrFBlockedError);

    expect(getPwToolsCoreSessionMocks().gotoPageWithNavigationGuard).toHaveBeenCalledTimes(1);
    expect(getPwToolsCoreSessionMocks().assertPageNavigationCompletedSafely).toHaveBeenCalledTimes(
      1,
    );
    // Navigate-style entry points OWN the navigation lifecycle, so when the
    // post-navigation safety check rejects with an SSRF policy error the
    // caller is responsible for closing the tab it just navigated. This is
    // the counterpart to the read-only paths (snapshot/screenshot/
    // interactions), which must NOT close the tab on the same error.
    expect(getPwToolsCoreSessionMocks().closeBlockedNavigationTarget).toHaveBeenCalledTimes(1);
    expect(getPwToolsCoreSessionMocks().closeBlockedNavigationTarget).toHaveBeenCalledWith({
      cdpUrl: "http://127.0.0.1:18792",
      page,
      targetId: undefined,
    });
  });

  it("does not close the tab when post-navigation rejection is not a policy deny", async () => {
    // Non-policy errors (e.g. transient playwright failures) must not be
    // treated as "we navigated to a blocked URL" — the tab stays open.
    const goto = vi.fn(async () => ({ request: () => undefined }));
    setPwToolsCoreCurrentPage({
      goto,
      url: vi.fn(() => "https://example.com/final"),
    });
    getPwToolsCoreSessionMocks().assertPageNavigationCompletedSafely.mockRejectedValueOnce(
      new Error("transient playwright error"),
    );

    await expect(
      mod.navigateViaPlaywright({
        cdpUrl: "http://127.0.0.1:18792",
        url: "https://example.com/final",
      }),
    ).rejects.toThrow("transient playwright error");

    expect(getPwToolsCoreSessionMocks().closeBlockedNavigationTarget).not.toHaveBeenCalled();
  });
});
