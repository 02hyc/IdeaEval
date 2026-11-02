import { NextResponse } from "next/server";
import { getConfig } from "@/lib/config";
import { buildTrace, findTraceRunDir, TraceError } from "@/lib/traces";

export const dynamic = "force-dynamic";

type RouteContext = {
  // Catch-all: the run id is a path relative to the runs root — ["<model-profile>", "<run-id>"]
  // when grouped by model profile, or ["<run-id>"] for the legacy flat layout.
  params: Promise<{ runId: string[] }>;
};

export async function GET(_req: Request, context: RouteContext) {
  try {
    const params = await context.params;
    const key = (params.runId ?? []).map((segment) => decodeURIComponent(segment)).join("/");
    const config = getConfig();
    const runDir = findTraceRunDir(config.traceRunsDir, key);
    return NextResponse.json(buildTrace(runDir));
  } catch (exc) {
    const message = exc instanceof Error ? exc.message : String(exc);
    if (exc instanceof TraceError) {
      return NextResponse.json(
        { error: message },
        { status: message.startsWith("invalid run id") ? 400 : 404 }
      );
    }
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
