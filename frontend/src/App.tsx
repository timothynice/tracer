import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { AppShell } from "./components/AppShell";
import { EmptyState } from "./components/EmptyState";
import { ExportMenu } from "./components/ExportMenu";
import { ImageMenu } from "./components/ImageMenu";
import { Inspector, InspectorOverlay } from "./components/Inspector";
import { SettingsSheet } from "./components/SettingsSheet";
import { SettingsView } from "./components/SettingsView";
import { Sidebar } from "./components/Sidebar";
import { TitleBar } from "./components/TitleBar";
import { VectorizePanel } from "./components/VectorizePanel";
import { Viewer, type ViewerHandle } from "./components/Viewer";
import { useExports } from "./hooks/useExports";
import { useLayerInspector } from "./hooks/useLayerInspector";
import { useSettings } from "./hooks/useSettings";
import { useWindowDrop } from "./hooks/useWindowDrop";
import { specsFor } from "./lib/schema";
import { commandForKey, isTyping, type Command } from "./lib/shortcuts";
import { platform, type OpenOutcome, type Settings, type ViewMode } from "./platform";
import { createLibrary, ENGINE, shownAnswer, type Catalog } from "./state/library";
import { useLibrary } from "./state/useLibrary";

const FORMATS = platform.kind === "native" ? "PNG, JPG, HEIC, etc." : "PNG, JPG, GIF, WebP, BMP";

export default function App() {
  // in the app, the web's context menu (Reload, Inspect Element) never shows, in either window; text fields keep theirs
  useEffect(() => {
    if (platform.kind !== "native") return;
    const block = (e: MouseEvent) => {
      if (!isTyping(e.target)) e.preventDefault();
    };
    document.addEventListener("contextmenu", block);
    return () => document.removeEventListener("contextmenu", block);
  }, []);
  return platform.windowRole() === "settings" ? <SettingsWindow /> : <MainWindow />;
}

/** The app's Settings window: the settings, saved as they change, in the window's own appearance. */
function SettingsWindow() {
  const { settings, change } = useSettings();
  return (
    <div className="min-h-dvh bg-background">
      <SettingsView settings={settings} onChange={change} />
    </div>
  );
}

function MainWindow() {
  const engines = useQuery({ queryKey: ["engines"], queryFn: ({ signal }) => platform.engines(signal) });
  const presets = useQuery({ queryKey: ["presets"], queryFn: ({ signal }) => platform.presets(signal) });
  const { settings, change: changeSettings } = useSettings();

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
  return <Workspace catalog={catalog} settings={settings} onSettingsChange={changeSettings} />;
}

const VIEW_KEY = "studi0trace.view";
// the commands that read the parameters or the traced SVG, which a number field still being typed in has not yet committed
const READS_SETTINGS = new Set<Command>(["generate", "export-svg", "export-png-1", "export-png-2", "export-png-4", "export-all", "copy-svg"]);
const MODES: ViewMode[] = ["split", "side", "overlay", "vector"];

function Workspace({ catalog, settings, onSettingsChange }: { catalog: Catalog; settings: Settings; onSettingsChange: (next: Settings) => void }) {
  // one library for the window's life; the settings reach it as they change
  const lib = useMemo(() => createLibrary(platform, catalog, settings), [catalog]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => lib.setSettings(settings), [lib, settings]);
  const state = useLibrary(lib);
  const item = state.selected ? (state.items.find((i) => i.image.id === state.selected) ?? null) : null;
  const answer = item ? shownAnswer(item) : null;
  const [sidebar, setSidebar] = useState(true);
  const [inspectorPane, setInspectorPane] = useState(true);
  const [dragging, setDragging] = useState(false);
  const [mode, setModeState] = useState<ViewMode>(() => {
    try {
      const saved = localStorage.getItem(VIEW_KEY) as ViewMode | null;
      return saved && MODES.includes(saved) ? saved : "split";
    } catch {
      return "split";
    }
  });
  const setMode = useCallback((m: ViewMode) => {
    setModeState(m);
    try {
      localStorage.setItem(VIEW_KEY, m);
    } catch {
      /* a remembered convenience, nothing more */
    }
  }, []);
  const viewerRef = useRef<ViewerHandle>(null);
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

  const busy = item?.job ? (item.job.phase === "queued" ? "Queued…" : item.job.key === "auto" ? `Trying ${catalog.presets.filter((p) => p.auto_candidate).length} presets…` : "Tracing…") : null;
  const viewer = item ? (
    <Viewer
      ref={viewerRef}
      sourceUrl={item.image.previewUrl}
      svg={layers.exportSvg}
      width={item.image.width}
      height={item.image.height}
      mode={mode}
      onModeChange={setMode}
      busy={busy}
      errorMessage={item.error?.message}
      onRetry={() => lib.generate(item.image.id)}
      display={{ points: layers.state.points, outlines: layers.state.outlines }}
      onDisplayChange={layers.patch}
      layersOpen={layers.state.open}
      onToggleLayers={() => layers.patch({ open: !layers.state.open })}
      marks={layers.doc ? (scale) => <InspectorOverlay doc={layers.doc!} state={layers.liveState} scale={scale} /> : undefined}
      panel={layers.doc && layers.state.open ? <Inspector doc={layers.doc} bytes={layers.exportSvg?.length ?? 0} elapsedMs={answer?.elapsedMs} engineLabel={catalog.engine.label} edited={layers.dropped.size > 0} state={layers.liveState} onChange={layers.patch} /> : undefined}
    />
  ) : (
    <EmptyState onOpen={() => void open(platform.pickImages())} onSample={(f) => openFiles([f])} />
  );

  const exports = useExports(state, item, layers.exportSvg, settings);
  const anyVector = state.items.some((i) => i.shown !== null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const openSettings = useCallback(() => {
    if (!platform.openSettingsWindow()) setSettingsOpen(true);
  }, []);

  const run = useCallback(
    (cmd: Command) => {
      const id = item?.image.id;
      switch (cmd) {
        case "open":
          return void open(platform.pickImages());
        case "settings":
          return openSettings();
        case "export-svg":
          return void exports.exportImage("svg", 1);
        case "export-png-1":
          return void exports.exportImage("png", 1);
        case "export-png-2":
          return void exports.exportImage("png", 2);
        case "export-png-4":
          return void exports.exportImage("png", 4);
        case "export-all":
          return void exports.exportAll();
        case "copy-svg":
          return void exports.copySvg();
        case "reveal":
          if (item?.image.path) void platform.reveal(item.image.path);
          return;
        case "zoom-in":
          return viewerRef.current?.zoomIn();
        case "zoom-out":
          return viewerRef.current?.zoomOut();
        case "zoom-actual":
          return viewerRef.current?.actualSize();
        case "zoom-fit":
          return viewerRef.current?.fit();
        case "mode-split":
        case "mode-side":
        case "mode-overlay":
        case "mode-vector":
          return setMode(cmd.slice("mode-".length) as ViewMode);
        case "toggle-sidebar":
          return setSidebar((v) => !v);
        case "toggle-inspector":
          return setInspectorPane((v) => !v);
        case "generate":
          if (id) lib.generate(id);
          return;
        case "cancel":
          if (id) lib.cancel(id);
          return;
        case "remove":
          if (id) lib.remove(id);
          return;
        case "clear":
          return lib.clear();
      }
    },
    [item, open, openSettings, exports, setMode, lib],
  );
  // the listeners subscribe once and call whatever `run` is now
  const runRef = useRef(run);
  runRef.current = run;
  // Both roads in (the menu bar, the keys of a browser) pass here. ⌘⌫ in a text field deletes text, it does not remove
  // the image; and what reads the settings (a trace, an export) waits for the field's own blur to commit its value.
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);
  const dispatch = useCallback((cmd: Command) => {
    const active = document.activeElement;
    if (isTyping(active)) {
      if (cmd === "remove") return;
      if (READS_SETTINGS.has(cmd)) {
        (active as HTMLElement).blur();
        clearTimeout(timer.current);
        timer.current = setTimeout(() => runRef.current(cmd), 0);
        return;
      }
    }
    runRef.current(cmd);
  }, []);
  useEffect(() => platform.onMenu(dispatch), [dispatch]);
  useEffect(() => {
    if (platform.kind !== "web") return;
    const onKey = (e: KeyboardEvent) => {
      const cmd = commandForKey(e);
      if (!cmd) return;
      if (cmd === "remove" && (e.repeat || isTyping(e.target))) return;
      e.preventDefault();
      dispatch(cmd);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dispatch]);

  const hasItems = state.items.length > 0 || state.failed.length > 0;
  const hasImage = !!item;
  const hasVector = !!layers.exportSvg;
  const hasPath = !!item?.image.path;
  const tracing = !!item?.job;
  useEffect(() => {
    platform.setMenuState({ hasItems, hasImage, hasVector, anyVector, hasPath, tracing, mode, sidebar, inspector: inspectorPane });
  }, [hasItems, hasImage, hasVector, anyVector, hasPath, tracing, mode, sidebar, inspectorPane]);
  const invalidField = item?.error?.code === "validation_error" ? (((item.error.detail as { loc?: unknown[] }[] | undefined)?.[0]?.loc?.[1] as string | undefined) ?? null) : null;

  const panel = item ? (
    <VectorizePanel
      item={item}
      catalog={catalog}
      specs={specs}
      invalidField={invalidField}
      onPick={(p) => lib.pickPreset(item.image.id, p)}
      onParam={(name, value) => lib.setParam(item.image.id, name, value)}
      onGenerate={() => lib.generate(item.image.id)}
      onCancel={() => lib.cancel(item.image.id)}
      exportMenu={<ExportMenu canExport={!!layers.exportSvg} anyVector={anyVector} onExport={(k, s) => void exports.exportImage(k, s)} onCopy={() => void exports.copySvg()} onExportAll={() => void exports.exportAll()} />}
    />
  ) : (
    <div className="flex h-full items-center justify-center p-6 text-center text-muted-foreground">Open an image to vectorize it.</div>
  );

  return (
    <>
      <AppShell
      titleBar={<TitleBar native={platform.kind === "native"} sidebar={sidebar} inspector={inspectorPane} onToggleSidebar={() => setSidebar((v) => !v)} onToggleInspector={() => setInspectorPane((v) => !v)} onSettings={openSettings} />}
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
            wrapCard={(it, card) => (
              <ImageMenu
                item={it}
                onSelect={() => lib.select(it.image.id)}
                onGenerate={() => lib.generate(it.image.id)}
                onExport={() => void exports.exportImage("svg", 1, it)}
                onReveal={() => it.image.path && void platform.reveal(it.image.path)}
                onRemove={() => lib.remove(it.image.id)}
              >
                {card}
              </ImageMenu>
            )}
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
      <SettingsSheet open={settingsOpen} onOpenChange={setSettingsOpen} settings={settings} onChange={onSettingsChange} />
    </>
  );
}
