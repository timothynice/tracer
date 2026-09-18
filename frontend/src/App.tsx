import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Actions } from "./components/Actions";
import { Canvas } from "./components/Canvas";
import { CompareTable } from "./components/CompareTable";
import { Dropzone } from "./components/Dropzone";
import { EngineTabs } from "./components/EngineTabs";
import { Header } from "./components/Header";
import { ParamPanel } from "./components/ParamPanel";
import { Samples } from "./components/Samples";
import { StatsStrip } from "./components/StatsStrip";
import { useHealth } from "./hooks/useHealth";
import { useParams } from "./hooks/useParams";
import { useUpload } from "./hooks/useUpload";
import { useVectorize } from "./hooks/useVectorize";
import { ApiError, getEngines } from "./lib/api";

const ENGINE_KEY = "studi0trace.engine";
const COMPARE_KEY = "studi0trace.compare";

export default function App() {
  const health = useHealth();
  const ready = health.status === "ok";
  const engines = useQuery({ queryKey: ["engines"], queryFn: ({ signal }) => getEngines(signal), enabled: ready });
  const upload = useUpload();
  const params = useParams(engines.data);

  const [engine, setEngine] = useState<string>(() => localStorage.getItem(ENGINE_KEY) ?? "");
  useEffect(() => {
    if (engines.data?.length && !engines.data.some((e) => e.id === engine)) setEngine(engines.data[0].id);
  }, [engines.data, engine]);
  const pickEngine = useCallback((id: string) => {
    setEngine(id);
    localStorage.setItem(ENGINE_KEY, id);
  }, []);

  const [compare, setCompare] = useState(() => localStorage.getItem(COMPARE_KEY) === "1");
  const toggleCompare = (v: boolean) => {
    setCompare(v);
    localStorage.setItem(COMPARE_KEY, v ? "1" : "0");
  };

  const active = engines.data?.find((e) => e.id === engine);
  const engineIds = useMemo(() => (compare ? (engines.data ?? []).map((e) => e.id) : active ? [active.id] : []), [compare, engines.data, active]);

  const trace = useVectorize({
    image: upload.image,
    engines: engineIds,
    params: params.values,
    enabled: ready && !!active,
    reupload: upload.reupload,
  });

  useEffect(() => {
    if (upload.error) toast.error(upload.error.message);
  }, [upload.error]);
  useEffect(() => {
    if (trace.error) toast.error(trace.error.message, { id: "trace-error" });
  }, [trace.error]);

  const invalidField = useMemo(() => {
    const err = trace.error;
    if (!(err instanceof ApiError) || err.code !== "validation_error") return null;
    const loc = (err.detail as { loc?: unknown[] }[] | undefined)?.[0]?.loc;
    return loc && loc[0] === engine ? String(loc[1]) : null;
  }, [trace.error, engine]);

  const onFile = useCallback((file: File) => void upload.upload(file), [upload]);
  const disabledReason = health.status === "down" ? "Server unreachable — retrying…" : ready ? undefined : "Waking the server…";
  const result = active ? trace.results?.[active.id] : undefined;

  // The workspace opens on the local preview, so the source is on screen while
  // the upload and the trace are still in flight.
  const view = upload.preview;
  const busy = upload.uploading || (!!view && !upload.error && trace.updating);
  const busyLabel = upload.uploading ? "Uploading…" : "Tracing…";
  // A request that never returned a result (502, network, timeout) has to reach
  // the canvas; a toast alone leaves it sitting on "No vector yet".
  const failure = result?.error
    ? `${active?.label ?? engine} failed: ${result.error.message}`
    : upload.error
      ? upload.error.message
      : trace.error && !result
        ? trace.error.message
        : undefined;
  const retry = useCallback(() => (upload.error ? upload.retry() : trace.refetch()), [upload, trace]);

  return (
    <div className="min-h-dvh">
      <Header health={health} onNew={view ? upload.clear : undefined} />

      <main className="mx-auto max-w-[1400px] px-4 py-6 md:px-6 md:py-8">
        {!view ? (
          <div className="motion-fade mx-auto max-w-2xl space-y-6 pt-6 md:pt-16">
            <div className="space-y-2 text-center">
              <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Raster in. Faithful vectors out.</h1>
              <p className="text-muted-foreground">Trace logos, flat art and gradient illustrations to SVG, and compare engines side by side.</p>
            </div>
            <Dropzone onFile={onFile} disabled={!ready} disabledReason={disabledReason} />
            <Samples onPick={onFile} disabled={!ready} />
            {health.status === "waking" && (
              <p className="text-center text-xs text-muted-foreground">Free-tier servers sleep after inactivity and take up to a minute to wake. Hang tight.</p>
            )}
          </div>
        ) : (
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_336px]">
            <div className="min-w-0 space-y-4">
              <div className="motion-rise">
                <Canvas
                  sourceUrl={view.previewUrl}
                  svg={result?.svg ?? undefined}
                  width={view.width}
                  height={view.height}
                  updating={busy}
                  busyLabel={busyLabel}
                  errorMessage={failure}
                  onRetry={retry}
                />
              </div>
              <div className="motion-rise card flex flex-wrap items-center justify-between gap-4 p-4 [animation-delay:70ms]">
                <StatsStrip engineLabel={active?.label ?? engine} result={result} updating={busy} />
                <Actions svg={result?.svg ?? undefined} filename={view.file.name} engine={engine} width={view.width} height={view.height} />
              </div>
              {compare && engines.data && <CompareTable engines={engines.data} results={trace.results} active={engine} onPick={pickEngine} updating={trace.updating} />}
            </div>

            <aside aria-label="Controls" className="motion-rise card h-fit p-4 [animation-delay:140ms] lg:sticky lg:top-20">
              {engines.data && active ? (
                <>
                  <EngineTabs engines={engines.data} value={active.id} onChange={pickEngine}>
                    {(e) => (
                      <ParamPanel
                        engine={e.id}
                        specs={params.specs[e.id] ?? []}
                        values={params.values[e.id] ?? e.defaults}
                        onChange={(name, value) => params.set(e.id, name, value)}
                        onReset={() => params.reset(e.id)}
                        invalidField={e.id === engine ? invalidField : null}
                      />
                    )}
                  </EngineTabs>
                  <label className="mt-6 flex items-center justify-between gap-3 border-t pt-4 text-sm">
                    <span>
                      <span className="font-medium">Compare all engines</span>
                      <span className="block text-xs text-muted-foreground">Run every engine and rank the results</span>
                    </span>
                    <input type="checkbox" className="h-4 w-4 accent-[hsl(var(--primary))]" checked={compare} onChange={(e) => toggleCompare(e.target.checked)} />
                  </label>
                </>
              ) : (
                <p className="text-sm text-muted-foreground">Loading engines…</p>
              )}
            </aside>
          </div>
        )}
      </main>
    </div>
  );
}
