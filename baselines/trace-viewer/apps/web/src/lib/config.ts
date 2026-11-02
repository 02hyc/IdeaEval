import path from "node:path";

export interface ServerConfig {
  traceRunsDir: string;
}

export function getConfig(env: NodeJS.ProcessEnv = process.env): ServerConfig {
  const base = env.INIT_CWD || process.cwd();
  const resolveFrom = (value: string) =>
    path.isAbsolute(value) ? value : path.resolve(base, value);
  return {
    traceRunsDir: env.TRACE_RUNS_DIR
      ? resolveFrom(env.TRACE_RUNS_DIR)
      : path.resolve(base, ".runs"),
  };
}
