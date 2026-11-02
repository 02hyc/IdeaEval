import path from "node:path";
import { describe, expect, it } from "vitest";
import { getConfig } from "./config";

describe("getConfig", () => {
  it("defaults TRACE_RUNS_DIR to .runs under INIT_CWD", () => {
    const config = getConfig({ INIT_CWD: "/repo" } as unknown as NodeJS.ProcessEnv);

    expect(config.traceRunsDir).toBe(path.resolve("/repo", ".runs"));
  });

  it("resolves relative TRACE_RUNS_DIR from INIT_CWD", () => {
    const config = getConfig({
      INIT_CWD: "/repo",
      TRACE_RUNS_DIR: "artifacts/runs",
    } as unknown as NodeJS.ProcessEnv);

    expect(config.traceRunsDir).toBe(path.resolve("/repo", "artifacts/runs"));
  });

  it("keeps absolute TRACE_RUNS_DIR", () => {
    const config = getConfig({
      INIT_CWD: "/repo",
      TRACE_RUNS_DIR: "/tmp/ideaeval-runs",
    } as unknown as NodeJS.ProcessEnv);

    expect(config.traceRunsDir).toBe("/tmp/ideaeval-runs");
  });
});
