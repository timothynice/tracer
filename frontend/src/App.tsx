export default function App() {
  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-[1400px] items-center gap-3 px-4 md:px-6">
          <img src="/brand/studi0trace-mark.svg" alt="" className="h-8 w-8" aria-hidden="true" />
          <span className="text-base font-semibold tracking-tight">Studi0Trace</span>
        </div>
      </header>
      <main className="mx-auto max-w-[1400px] px-4 py-8 md:px-6">
        <p className="text-muted-foreground">Workspace coming up.</p>
      </main>
    </div>
  );
}
