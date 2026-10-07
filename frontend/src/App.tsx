import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { AppShell } from "./components/AppShell";
import { DriftCheck } from "./components/DriftCheck";
import { EmptyState } from "./components/EmptyState";
import { ExportMenu } from "./components/ExportMenu";
import { ImageMenu } from "./components/ImageMenu";
import { Inspector, InspectorOverlay } from "./components/Inspector";
import { RedrawConsentSheet } from "./components/RedrawConsentSheet";
import { RedrawSection } from "./components/RedrawSection";
import { SettingsSheet } from "./components/SettingsSheet";
import { SettingsView } from "./components/SettingsView";
import { Sidebar } from "./components/Sidebar";
import { TitleBar } from "./components/TitleBar";
import { VectorizePanel } from "./components/VectorizePanel";
import { Viewer, type ViewerHandle } from "./components/Viewer";
import { useExports } from "./hooks/useExports";
import { revealInFinder } from "./hooks/reveal";
import { useLayerInspector } from "./hooks/useLayerInspector";
import { useRedrawKey } from "./hooks/useRedrawKey";
import { useSettings } from "./hooks/useSettings";
import { useWindowDrop } from "./hooks/useWindowDrop";
import { redrawFailureText } from "./lib/redraw";
import { loadSample } from "./lib/samples";
import { specsFor } from "./lib/schema";
import { commandForKey, isTyping, type Command } from "./lib/shortcuts";
import { platform, type OpenOutcome, type Settings, type ViewMode } from "./platform";
import { createLibrary, ENGINE, currentJob, errorOf, redrawOf, shownAnswer, unexportedCount, type Catalog } from "./state/library";
import { useLibrary } from "./state/useLibrary";

const FORMATS = platform.kind === "native" ? "PNG, JPG, HEIC, etc." : "PNG, JPG, GIF, WebP, BMP";
// what the empty state lists: the app converts HEIC and TIFF with sips, a browser sends only these five
const DROP_FORMATS = platform.kind === "native" ? "PNG, JPEG, GIF, WebP, BMP, HEIC or TIFF" : "PNG, JPEG, GIF, WebP or BMP";
/** AI redraw runs in the Mac app only: a browser has no Keychain and no road to OpenAI. */
const CAN_REDRAW = platform.kind === "native";
/** A redraw action that failed, in the app's own words by code (never the message: it can quote a key); a cancel says nothing. */
const failRedraw = (err: unknown) => {
  const text = redrawFailureText(err);
  if (text) toast.error(text);
};

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
  const key = useRedrawKey();
  return (
    <div className="min-h-dvh bg-background">
      <SettingsView settings={settings} onChange={change} redraw={CAN_REDRAW ? { stored: key.stored, onSave: key.save, onRemove: key.remove } : undefined} />
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
  const visibleError = item ? errorOf(item) : null;
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

  // AI redraw: the consent sheet comes when a redraw finds no key; Show Original reviews the redraw in use
  const redrawKey = useRedrawKey();
  const [consentFor, setConsentFor] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const inspectorShown = useRef(inspectorPane);
  inspectorShown.current = inspectorPane;
  const startRedraw = useCallback(
    async (id: string) => {
      setReviewing(null);
      try {
        const failed = await lib.redraw(id);
        if (failed?.code === "no_key") setConsentFor(id);
        else if (!inspectorShown.current) {
          // the inspector's section is where a failure is shown; with it hidden the failure speaks as a toast
          const error = lib.getState().items.find((i) => i.image.id === id)?.redraw?.error;
          if (error) failRedraw(error);
        }
      } catch (err) {
        failRedraw(err);
      }
    },
    [lib],
  );
  const revert = useCallback(
    (id: string) => {
      setReviewing(null);
      void lib.revertRedraw(id).catch(failRedraw);
    },
    [lib],
  );
  const selectedId = item?.image.id;
  const roughKnown = item?.rough !== undefined;
  useEffect(() => {
    if (CAN_REDRAW && selectedId && settings.suggestRedraw && !roughKnown) lib.checkRough(selectedId);
  }, [lib, selectedId, roughKnown, settings.suggestRedraw]);

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
  useEffect(() => platform.onOpenFailures((failures) => lib.add(failures.map((failed) => ({ failed })))), [lib]);
  useEffect(() => platform.onDragState(setDragging), []);
  useWindowDrop(platform.kind === "web", openFiles, setDragging);

  const layers = useLayerInspector(answer?.svg, item?.image.id, useMemo(() => state.items.map((i) => i.image.id), [state.items]));

  const job = item ? currentJob(item) : null;
  const busy = job ? (job.phase === "queued" ? "Queued…" : job.key === "auto" ? `Trying ${catalog.presets.filter((p) => p.auto_candidate).length} presets…` : "Tracing…") : null;
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
      errorMessage={visibleError?.message}
      onRetry={() => lib.generate(item.image.id)}
      display={{ points: layers.state.points, outlines: layers.state.outlines }}
      onDisplayChange={layers.patch}
      layersOpen={layers.state.open}
      onToggleLayers={() => layers.patch({ open: !layers.state.open })}
      marks={layers.doc ? (scale) => <InspectorOverlay doc={layers.doc!} state={layers.liveState} scale={scale} /> : undefined}
      panel={layers.doc && layers.state.open ? <Inspector doc={layers.doc} bytes={layers.exportSvg?.length ?? 0} elapsedMs={answer?.elapsedMs} engineLabel={catalog.engine.label} edited={layers.dropped.size > 0} state={layers.liveState} onChange={layers.patch} onToggle={layers.toggle} /> : undefined}
    />
  ) : (
    <EmptyState formats={DROP_FORMATS} canDownscale={platform.kind === "native"} onOpen={() => void open(platform.pickImages())} onSample={(name) => void open(loadSample(name).then((f) => platform.openFiles([f])))} />
  );
  // a redraw waiting for its decision takes the canvas: nothing changes before the drift is seen
  const pending = item?.redraw?.pending ?? null;
  const review = item && reviewing === item.image.id ? item.redraw : undefined;
  const main =
    item && pending ? (
      <DriftCheck
        original={item.redraw?.original ?? item.image}
        redraw={pending.redraw}
        drift={pending.drift}
        mode={mode}
        onModeChange={setMode}
        decide={{
          onUse: () => void lib.acceptRedraw(item.image.id).catch(failRedraw),
          onTryAgain: () => void startRedraw(item.image.id),
          onDiscard: () => lib.discardRedraw(item.image.id),
        }}
      />
    ) : item && review?.active && review.original && review.drift ? (
      <DriftCheck original={review.original} redraw={item.image} drift={review.drift} mode={mode} onModeChange={setMode} onClose={() => setReviewing(null)} />
    ) : (
      viewer
    );

  const exports = useExports(state, item, layers.exportSvg, settings, lib.markExported, layers.exportSvgFor);
  const anyVector = state.items.some((i) => i.shown !== null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const openSettings = useCallback(() => {
    if (!platform.openSettingsWindow()) setSettingsOpen(true);
  }, []);

  // Clear All asks first when a traced vector would be lost (the menu item and the sidebar's button both come here).
  const asking = useRef(false);
  const clearAll = useCallback(async () => {
    if (asking.current) return;
    const lost = unexportedCount(lib.getState());
    if (lost > 0) {
      asking.current = true;
      const clear = await platform.confirmClear(lost).catch(() => false);
      asking.current = false;
      if (!clear) return;
    }
    lib.clear();
  }, [lib]);

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
          if (item?.image.path) void revealInFinder(item.image.path);
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
          return clearAll();
        case "redraw":
          if (id && CAN_REDRAW) void startRedraw(id);
          return;
        case "show-original":
          if (id && item?.redraw?.active) setReviewing(id);
          return;
        case "revert-redraw":
          if (id && item?.redraw?.active) revert(id);
          return;
      }
    },
    [item, open, openSettings, exports, setMode, lib, clearAll, startRedraw, revert],
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
      // the Settings sheet is modal: the image behind it is not exported, traced or removed from its keys
      if (settingsOpen && cmd !== "settings") return;
      if (cmd === "remove" && (e.repeat || isTyping(e.target))) return;
      e.preventDefault();
      dispatch(cmd);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dispatch, settingsOpen]);

  const hasItems = state.items.length > 0 || state.failed.length > 0;
  const hasImage = !!item;
  const hasVector = !!layers.exportSvg;
  const hasPath = !!item?.image.path;
  const tracing = !!job;
  const anyTracing = state.items.some((i) => i.job);
  const unexported = unexportedCount(state);
  const redraw = item ? redrawOf(item) : null;
  const canRedraw = CAN_REDRAW && hasImage && !redraw?.phase;
  const isRedraw = !!redraw?.active;
  useEffect(() => {
    platform.setMenuState({ hasItems, hasImage, hasVector, anyVector, hasPath, tracing, anyTracing, unexported, mode, sidebar, inspector: inspectorPane, canRedraw, isRedraw });
  }, [hasItems, hasImage, hasVector, anyVector, hasPath, tracing, anyTracing, unexported, mode, sidebar, inspectorPane, canRedraw, isRedraw]);
  const invalidField = visibleError?.code === "validation_error" ? (((visibleError.detail as { loc?: unknown[] }[] | undefined)?.[0]?.loc?.[1] as string | undefined) ?? null) : null;

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
      redraw={
        CAN_REDRAW ? (
          <RedrawSection
            item={item}
            suggest={settings.suggestRedraw}
            onRedraw={() => void startRedraw(item.image.id)}
            onCancel={() => lib.cancelRedraw(item.image.id)}
            onShowOriginal={() => setReviewing(item.image.id)}
            onRevert={() => revert(item.image.id)}
          />
        ) : undefined
      }
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
            onClear={() => void clearAll()}
            onDownscale={(i) => void lib.downscale(i).catch((err: Error) => toast.error(err.message))}
            onDismissFailure={lib.dismissFailure}
            wrapCard={(it, card) => (
              <ImageMenu
                item={it}
                onSelect={() => lib.select(it.image.id)}
                onGenerate={() => lib.generate(it.image.id)}
                onExport={() => void exports.exportImage("svg", 1, it)}
                onReveal={() => it.image.path && void revealInFinder(it.image.path)}
                onRemove={() => lib.remove(it.image.id)}
                redraw={CAN_REDRAW ? { onRedraw: () => void startRedraw(it.image.id), onShowOriginal: () => setReviewing(it.image.id), onRevert: () => revert(it.image.id) } : undefined}
              >
                {card}
              </ImageMenu>
            )}
          />
        ) : null
      }
      main={main}
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
      <RedrawConsentSheet
        open={consentFor !== null}
        onOpenChange={(isOpen) => {
          if (!isOpen) setConsentFor(null);
        }}
        onSave={async (key) => {
          await redrawKey.save(key);
          const id = consentFor;
          setConsentFor(null);
          if (id) void startRedraw(id);
        }}
      />
    </>
  );
}
