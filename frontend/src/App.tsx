import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { AppShell } from "./components/AppShell";
import { Canvas } from "./components/Canvas";
import { EmptyState } from "./components/EmptyState";
import { Inspector, InspectorOverlay } from "./components/Inspector";
import { ParamPanel } from "./components/ParamPanel";
import { Presets } from "./components/Presets";
import { Sidebar } from "./components/Sidebar";
import { TitleBar } from "./components/TitleBar";
import { useLayerInspector } from "./hooks/useLayerInspector";
import { useWindowDrop } from "./hooks/useWindowDrop";
import { specsFor } from "./lib/schema";
import { applyTheme, watchSystemTheme } from "./lib/theme";
import { DEFAULT_SETTINGS, platform, type OpenOutcome, type Settings } from "./platform";
import { createLibrary, ENGINE, shownAnswer, type Catalog } from "./state/library";
import { useLibrary } from "./state/useLibrary";

const FORMATS = platform.kind === "native" ? "PNG, JPG, HEIC, etc." : "PNG, JPG, GIF, WebP, BMP";

export default function App() {
  const engines = useQuery({ queryKey: ["engines"], queryFn: ({ signal }) => platform.engines(signal) });
  const presets = useQuery({ queryKey: ["presets"], queryFn: ({ signal }) => platform.presets(signal) });
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  useEffect(() => {
    void platform.loadSettings().then(setSettings);
    return platform.onSettings(setSettings);
  }, []);
  useEffect(() => {
    applyTheme(settings.appearance);
    return watchSystemTheme(() => settings.appearance);
  }, [settings.appearance]);

  const engine = engines.data?.find((e) => e.id === ENGINE);
  const catalog = useMemo<Catalog | null>(() => (engine && presets.data ? { engine, presets: presets.data.filter((p) => p.engine === ENGINE) } : null), [engine, presets.data]);
  const failure = engines.error ?? presets.error;
  if (failure || !catalog) {
    return (
      <AppShell
        titleBar={<TitleBar native={platform.kind === "native"} sidebar={false} inspector={false} onToggleSidebar={() => {}} onToggleInspector={() => {}} onSettings={() => {}} />}
        sidebar={null}
        inspector={null}
        main={
          <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center" role={failure ? "alert" : "status"}>
            <p className="text-[15px] font-semibold">{failure ? "Studi0Trace could not start" : "Starting…"}</p>
            {failure && <p className="max-w-sm text-muted-foreground">{failure.message}</p>}
            {failure && (
              <button type="button" className="mac-button" onClick={() => void Promise.all([engines.refetch(), presets.refetch()])}>
                Try Again
              </button>
            )}
          </div>
        }
      />
    );
  }
  return <Workspace catalog={catalog} settings={settings} />;
}

function Workspace({ catalog, settings }: { catalog: Catalog; settings: Settings }) {
  // one library for the window's life; the settings reach it as they change
  const lib = useMemo(() => createLibrary(platform, catalog, settings), [catalog]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => lib.setSettings(settings), [lib, settings]);
  const state = useLibrary(lib);
  const item = state.selected ? (state.items.find((i) => i.image.id === state.selected) ?? null) : null;
  const answer = item ? shownAnswer(item) : null;
  const [sidebar, setSidebar] = useState(true);
  const [inspectorPane, setInspectorPane] = useState(true);
  const [dragging, setDragging] = useState(false);
  const specs = useMemo(() => specsFor(catalog.engine), [catalog.engine]);

  const open = useCallback(
    async (outcomes: Promise<OpenOutcome[]>) => {
      try {
        lib.add(await outcomes);
      } catch (err) {
        toast.error((err as Error).message);
      }
    },
    [lib],
  );
  const openFiles = useCallback((files: File[]) => void open(platform.openFiles(files)), [open]);
  useEffect(() => platform.onOpenPaths((paths) => void open(platform.openPaths(paths))), [open]);
  useEffect(() => platform.onDragState(setDragging), []);
  useWindowDrop(platform.kind === "web", openFiles, setDragging);

  const layers = useLayerInspector(answer?.svg);

  // ── the viewer (Task 9 replaces this with <Viewer>)
  const viewer = item ? (
    <Canvas
      sourceUrl={item.image.previewUrl}
      svg={layers.exportSvg}
      width={item.image.width}
      height={item.image.height}
      updating={!!item.job}
      busyLabel={item.job?.phase === "queued" ? "Queued…" : item.job?.key === "auto" ? "Trying presets…" : "Tracing…"}
      errorMessage={item.error?.message}
      onRetry={() => lib.generate(item.image.id)}
      display={{ points: layers.state.points, outlines: layers.state.outlines }}
      onDisplayChange={layers.patch}
      marks={layers.doc ? (scale) => <InspectorOverlay doc={layers.doc!} state={layers.liveState} scale={scale} /> : undefined}
      panel={
        layers.doc && layers.state.open ? (
          <Inspector doc={layers.doc} bytes={layers.exportSvg?.length ?? 0} elapsedMs={answer?.elapsedMs} engineLabel={catalog.engine.label} edited={layers.dropped.size > 0} state={layers.liveState} onChange={layers.patch} />
        ) : undefined
      }
    />
  ) : (
    <EmptyState onOpen={() => void open(platform.pickImages())} onSample={(f) => openFiles([f])} />
  );

  // ── the right panel (Task 10 replaces this with <VectorizePanel>)
  const panel = item ? (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
        <Presets
          presets={catalog.presets}
          defaults={catalog.engine.defaults}
          values={item.params}
          onPick={(p) => lib.pickPreset(item.image.id, p)}
          active={item.preset === "auto" ? "auto" : (item.preset ?? undefined)}
          auto={item.auto}
          autoRunning={item.job?.key === "auto"}
        />
        <ParamPanel engine={ENGINE} specs={specs} values={item.params} onChange={(name, value) => lib.setParam(item.image.id, name, value)} />
      </div>
      <div className="border-t p-3">
        <button type="button" className="mac-primary" onClick={() => lib.generate(item.image.id)} disabled={!!item.job}>
          Generate Vector
        </button>
      </div>
    </div>
  ) : (
    <p className="p-4 text-muted-foreground">Open an image to vectorize it.</p>
  );

  return (
    <AppShell
      titleBar={
        <TitleBar
          native={platform.kind === "native"}
          sidebar={sidebar}
          inspector={inspectorPane}
          onToggleSidebar={() => setSidebar((v) => !v)}
          onToggleInspector={() => setInspectorPane((v) => !v)}
          onSettings={() => platform.openSettingsWindow()}
        />
      }
      sidebar={
        sidebar ? (
          <Sidebar
            items={state.items}
            failed={state.failed}
            selected={state.selected}
            formats={FORMATS}
            canDownscale={platform.kind === "native"}
            onAdd={() => void open(platform.pickImages())}
            onSelect={lib.select}
            onSelectNext={lib.selectNext}
            onClear={lib.clear}
            onDownscale={(i) => void lib.downscale(i)}
            onDismissFailure={lib.dismissFailure}
          />
        ) : null
      }
      main={viewer}
      inspector={inspectorPane ? panel : null}
      overlay={
        dragging ? (
          <div className="pointer-events-none fixed inset-2 top-[60px] z-50 flex items-center justify-center rounded-xl border-2 border-dashed mac-tint" style={{ borderColor: "var(--accent-mac)" }}>
            <p className="text-[15px] font-semibold">Drop to add</p>
          </div>
        ) : null
      }
    />
  );
}
