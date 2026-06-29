import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import {
  buildScriptEvidenceSummary,
  QA_EVIDENCE_FILENAME,
  type QaEvidenceStatus,
  type QaEvidenceSummaryJson,
} from "../../../../extensions/qa-lab/src/evidence-summary.js";

const SCENARIO_ID = "voice-call-cli-rpc-agent-tool";
const SCENARIO_TITLE = "Voice Call CLI, RPC, and agent-tool evidence";
const SOURCE_PATH = "test/e2e/qa-lab/channels/voice-call-cli-rpc-agent-tool.ts";
const PRIMARY_COVERAGE_ID = "voice-call.cli-rpc-agent-tool";
const TEST_FILES = [
  "extensions/voice-call/src/cli.test.ts",
  "extensions/voice-call/src/gateway-continue-operation.test.ts",
  "extensions/voice-call/src/runtime.test.ts",
  "extensions/voice-call/src/response-generator.test.ts",
] as const;

type ProducerOptions = {
  artifactBase: string;
  repoRoot: string;
};

type ProofResult = {
  artifacts: Array<{ kind: string; path: string }>;
  details?: string;
  durationMs: number;
  status: QaEvidenceStatus;
};

function parseOptions(argv = process.argv.slice(2)): ProducerOptions {
  let artifactBase = path.join(".artifacts", "qa-e2e", SCENARIO_ID);
  let repoRoot = process.cwd();

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--artifact-base") {
      artifactBase = argv[++index] ?? "";
      continue;
    }
    if (arg === "--repo-root") {
      repoRoot = argv[++index] ?? "";
      continue;
    }
    if (arg === "--help") {
      printUsage();
      process.exit(0);
    }
    throw new Error(`unknown argument: ${arg}`);
  }

  if (!artifactBase.trim()) {
    throw new Error("--artifact-base must not be empty");
  }
  if (!repoRoot.trim()) {
    throw new Error("--repo-root must not be empty");
  }
  return { artifactBase, repoRoot };
}

function printUsage() {
  console.log(`Usage: node --import tsx ${SOURCE_PATH} [options]

Options:
  --artifact-base <dir>  Directory for qa-evidence.json and proof logs
  --repo-root <dir>      Repository root, defaults to cwd
`);
}

function relativeArtifactPath(options: ProducerOptions, filePath: string) {
  return path.relative(options.artifactBase, filePath).replaceAll(path.sep, "/");
}

function formatErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function appendText(filePath: string, text: string) {
  await fs.appendFile(filePath, text, "utf8");
}

async function runVitestProof(options: ProducerOptions): Promise<ProofResult> {
  const startedAt = Date.now();
  await fs.mkdir(options.artifactBase, { recursive: true });
  const logPath = path.join(options.artifactBase, "voice-call-vitest.log");
  await fs.writeFile(
    logPath,
    [
      "Voice Call CLI/RPC/agent-tool QA proof",
      `repoRoot=${options.repoRoot}`,
      `tests=${TEST_FILES.join(", ")}`,
      "",
    ].join("\n"),
    "utf8",
  );

  try {
    const result = await runNodeCommand({
      args: ["scripts/run-vitest.mjs", ...TEST_FILES, "--reporter=verbose"],
      cwd: options.repoRoot,
      logPath,
    });
    if (result.exitCode !== 0) {
      return {
        artifacts: [{ kind: "log", path: relativeArtifactPath(options, logPath) }],
        details: `Voice Call Vitest proof failed with exit code ${result.exitCode}`,
        durationMs: Math.max(1, Date.now() - startedAt),
        status: "fail",
      };
    }
    return {
      artifacts: [{ kind: "log", path: relativeArtifactPath(options, logPath) }],
      details:
        "Voice Call CLI helpers, Gateway continue operation, realtime consult tool, and embedded response generator tests passed.",
      durationMs: Math.max(1, Date.now() - startedAt),
      status: "pass",
    };
  } catch (error) {
    const details = formatErrorMessage(error);
    await appendText(logPath, `\nfail: ${details}\n`);
    return {
      artifacts: [{ kind: "log", path: relativeArtifactPath(options, logPath) }],
      details,
      durationMs: Math.max(1, Date.now() - startedAt),
      status: "fail",
    };
  }
}

function runNodeCommand(params: {
  args: string[];
  cwd: string;
  logPath: string;
}): Promise<{ exitCode: number | null }> {
  return new Promise((resolve, reject) => {
    const output: string[] = [];
    const child = spawn(process.execPath, params.args, {
      cwd: params.cwd,
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
    });

    child.stdout.on("data", (chunk: Buffer) => {
      output.push(chunk.toString("utf8"));
    });
    child.stderr.on("data", (chunk: Buffer) => {
      output.push(chunk.toString("utf8"));
    });
    child.on("error", reject);
    child.on("close", (exitCode) => {
      appendText(params.logPath, output.join("")).then(() => resolve({ exitCode }), reject);
    });
  });
}

function buildEvidence(params: {
  options: ProducerOptions;
  result: ProofResult;
}): QaEvidenceSummaryJson {
  return buildScriptEvidenceSummary({
    artifactPaths: params.result.artifacts,
    evidenceMode: "full",
    env: process.env,
    generatedAt: new Date().toISOString(),
    primaryModel: "mock-openai/gpt-5.5",
    providerMode: "mock-openai",
    repoRoot: params.options.repoRoot,
    runner: "script",
    targets: [
      {
        id: SCENARIO_ID,
        title: SCENARIO_TITLE,
        sourcePath: SOURCE_PATH,
        primaryCoverageIds: [PRIMARY_COVERAGE_ID],
        docsRefs: [
          "docs/cli/voicecall.md",
          "docs/plugins/voice-call.md",
          "docs/gateway/protocol.md",
          "docs/help/testing.md",
        ],
        codeRefs: [
          SOURCE_PATH,
          ...TEST_FILES,
          "extensions/voice-call/src/cli.ts",
          "extensions/voice-call/src/gateway-continue-operation.ts",
          "extensions/voice-call/src/runtime.ts",
          "extensions/voice-call/src/response-generator.ts",
        ],
      },
    ],
    results: [
      {
        id: SCENARIO_ID,
        status: params.result.status,
        durationMs: params.result.durationMs,
        failureMessage: params.result.details,
      },
    ],
  });
}

async function writeJson(filePath: string, value: unknown) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

export async function runVoiceCallCliRpcAgentToolProducer(
  options: ProducerOptions,
): Promise<QaEvidenceSummaryJson> {
  const result = await runVitestProof(options);
  const evidence = buildEvidence({ options, result });
  await writeJson(path.join(options.artifactBase, QA_EVIDENCE_FILENAME), evidence);
  await writeJson(path.join(options.artifactBase, "latest-run.json"), {
    qaEvidence: QA_EVIDENCE_FILENAME,
    scenarioId: SCENARIO_ID,
    status: result.status,
  });
  return evidence;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  runVoiceCallCliRpcAgentToolProducer(parseOptions()).then(
    (evidence) => {
      const entry = evidence.entries.find((candidate) => candidate.test.id === SCENARIO_ID);
      if (entry?.result.status !== "pass") {
        process.exitCode = 1;
      }
    },
    (error: unknown) => {
      console.error(formatErrorMessage(error));
      process.exitCode = 1;
    },
  );
}
