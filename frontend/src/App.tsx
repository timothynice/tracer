import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Layers } from "lucide-react";
import { toast } from "sonner";

import { Actions } from "./components/Actions";
import { Canvas } from "./components/Canvas";
import { Dropzone } from "./components/Dropzone";
import { EngineTabs } from "./components/EngineTabs";
import { Header } from "./components/Header";
import { EMPTY_INSPECTOR, Inspector, InspectorOverlay, tinyShapes, type InspectorState } from "./components/Inspector";
import { ParamPanel } from "./components/ParamPanel";
import { Presets } from "./components/Presets";
import { Samples } from "./components/Samples";
import { useHealth } from "./hooks/useHealth";
import { useParams } from "./hooks/useParams";
import { useUpload } from "./hooks/useUpload";
import { useVectorize } from "./hooks/useVectorize";
import { ApiError, getEngines, getPresets } from "./lib/api";
import { parseSvg } from "./lib/svgdoc";

const ENGINE_KEY = "studi0trace.engine";

export default function App() {
  const health = useHealth();
  const ready = health.status === "ok";
  const engines = useQuery({ queryKey: ["engines"], queryFn: ({ signal }) => getEngines(signal), enabled: ready });
  const presets = useQuery({ queryKey: ["presets"], queryFn: ({ signal }) => getPresets(signal), enabled: ready });
  const upload = useUpload();
  const params = useParams(engines.data);

  // The app shows the engines the backend marks primary. The others stay
  // callable for benchmarking and comparison, just not in the product's face.
  const shown = useMemo(() => (engines.data ?? []).filter((e) => e.primary), [engines.data]);
  const [engine, setEngine] = useState<string>(() => localStorage.getItem(ENGINE_KEY) ?? "");
  useEffect(() => {
    if (shown.length && !shown.some((e) => e.id === engine)) setEngine(shown[0].id);
  }, [shown, engine]);
  const pickEngine = useCallback((id: string) => {
    setEngine(id);
    localStorage.setItem(ENGINE_KEY, id);
  }, []);

  const active = shown.find((e) => e.id === engine);
  const engineIds = useMemo(() => (active ? [active.id] : []), [active]);

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

  // Inspection works on the SVG the engine returned; export gets the cleaned one.
  const [inspector, setInspector] = useState<InspectorState>(EMPTY_INSPECTOR);
  const patchInspector = useCallback((patch: Partial<InspectorState>) => setInspector((prev) => ({ ...prev, ...patch })), []);
  const doc = useMemo(() => (result?.svg ? parseSvg(result.svg) : null), [result?.svg]);
  // A new trace invalidates shape indices, so per-shape state cannot carry over.
  useEffect(() => setInspector((prev) => ({ ...prev, hidden: new Set(), highlight: null, minArea: 0 })), [result?.svg]);
  const dropped = useMemo(() => {
    if (!doc) return new Set<number>();
    const out = new Set(inspector.hidden);
    for (const i of tinyShapes(doc, inspector.minArea)) out.add(i);
    return out;
  }, [doc, inspector.hidden, inspector.minArea]);
  const exportSvg = useMemo(() => (doc && dropped.size ? doc.render(dropped) : result?.svg ?? undefined), [doc, dropped, result?.svg]);
  const liveState = useMemo(() => ({ ...inspector, hidden: dropped }), [inspector, dropped]);

  return (
    // A fixed viewport: the app never scrolls as a page, only inside its panels.
    <div className="flex h-dvh flex-col overflow-hidden">
      <Header health={health} onNew={view ? upload.clear : undefined} />

      <main className="mx-auto flex w-full min-h-0 max-w-[1600px] flex-1 flex-col px-4 py-4 md:px-6 md:py-5">
        {!view ? (
          <div className="motion-fade mx-auto w-full max-w-2xl space-y-6 overflow-y-auto pt-6 md:pt-16">
            <div className="space-y-2 text-center">
              <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Raster in. Faithful vectors out.</h1>
              <p className="text-muted-foreground">Trace logos, flat art and gradient illustrations to faithful SVG.</p>
            </div>
            <Dropzone onFile={onFile} disabled={!ready} disabledReason={disabledReason} />
            <Samples onPick={onFile} disabled={!ready} />
            {health.status === "waking" && (
              <p className="text-center text-xs text-muted-foreground">Free-tier servers sleep after inactivity and take up to a minute to wake. Hang tight.</p>
            )}
          </div>
        ) : (
          <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
            <div className="motion-rise flex min-h-0 min-w-0 flex-col gap-3">
              <Canvas
                sourceUrl={view.previewUrl}
                svg={exportSvg}
                width={view.width}
                height={view.height}
                updating={busy}
                busyLabel={busyLabel}
                errorMessage={failure}
                onRetry={retry}
                display={{ points: inspector.points, outlines: inspector.outlines }}
                onDisplayChange={patchInspector}
                marks={doc ? (scale) => <InspectorOverlay doc={doc} state={liveState} scale={scale} /> : undefined}
                panel={
                  doc ? (
                    inspector.open ? (
                      <Inspector doc={doc} bytes={exportSvg?.length ?? 0} elapsedMs={result?.elapsed_ms} engineLabel={active?.label ?? engine} edited={dropped.size > 0} state={liveState} onChange={patchInspector} />
                    ) : (
                      <button
                        type="button"
                        data-overlay-ui
                        className="btn-ghost btn-icon absolute left-3 top-3 z-30 h-9 w-9 border bg-card/90 shadow-sm backdrop-blur"
                        aria-label="Inspect the vector"
                        title="Inspect the vector"
                        onClick={() => patchInspector({ open: true })}
                      >
                        <Layers className="h-4 w-4" aria-hidden="true" />
                      </button>
                    )
                  ) : undefined
                }
              />
              <div className="card shrink-0 px-3 py-2.5">
                <Actions svg={exportSvg} filename={view.file.name} engine={engine} width={view.width} height={view.height} />
              </div>
            </div>

            <aside aria-label="Controls" className="motion-rise card flex min-h-0 flex-col overflow-hidden [animation-delay:120ms]">
              {engines.data && active ? (
                <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
                  {presets.data && presets.data.some((p) => p.engine === active.id) && (
                    <div className="border-b pb-4">
                      <Presets
                        presets={presets.data.filter((p) => p.engine === active.id)}
                        defaults={active.defaults}
                        values={params.values[active.id] ?? active.defaults}
                        onPick={(p) => params.apply(active.id, p.params)}
                      />
                    </div>
                  )}
                  {shown.length > 1 && (
                    <div className="border-b py-3">
                      <EngineTabs engines={shown} value={active.id} onChange={pickEngine}>
                        {() => null}
                      </EngineTabs>
                    </div>
                  )}
                  <ParamPanel
                    engine={active.id}
                    specs={params.specs[active.id] ?? []}
                    values={params.values[active.id] ?? active.defaults}
                    onChange={(name, value) => params.set(active.id, name, value)}
                    invalidField={invalidField}
                  />
                </div>
              ) : (
                <p className="p-4 text-sm text-muted-foreground">Loading engines…</p>
              )}
            </aside>
          </div>
        )}
      </main>
    </div>
  );
}
