import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useEffect, useState } from "react";

import { CONSENT_CHANGES, CONSENT_UPLOAD, PRIVACY_SENTENCE } from "@/lib/redraw";

export interface RedrawConsentSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Stores the key (in the Keychain, through the platform); a rejection's message is shown and the sheet stays. */
  onSave: (key: string) => Promise<void>;
}

/** AI redraw's first use: what is uploaded, to whom and on whose bill, and the key. It comes only when no key is stored. */
export function RedrawConsentSheet({ open, onOpenChange, onSave }: RedrawConsentSheetProps) {
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!open) {
      setKey("");
      setError(null);
      setSaving(false);
    }
  }, [open]);
  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave(key.trim());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/30" />
        <Dialog.Content aria-describedby="redraw-consent-text" className="fixed left-1/2 top-16 z-50 w-[460px] max-w-[calc(100vw-32px)] -translate-x-1/2 space-y-4 rounded-xl border bg-background p-5 shadow-elevated">
          <div className="flex items-center justify-between">
            <Dialog.Title className="text-[15px] font-semibold">Redraw with AI</Dialog.Title>
            <Dialog.Close className="mac-icon" aria-label="Close">
              <X className="h-4 w-4" aria-hidden="true" />
            </Dialog.Close>
          </div>
          <div id="redraw-consent-text" className="space-y-2 text-[13px] leading-snug">
            <p>{CONSENT_UPLOAD}</p>
            <p>{CONSENT_CHANGES}</p>
            <p className="text-muted-foreground">{PRIVACY_SENTENCE}</p>
          </div>
          <form
            className="space-y-2"
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <label htmlFor="redraw-openai-key" className="block text-[12px] font-medium">
              OpenAI API key
            </label>
            <input
              id="redraw-openai-key"
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder="sk-…"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              className="h-9 w-full rounded-md border bg-background px-2.5 text-[13px] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/20"
            />
            <p className="text-[11px] text-muted-foreground">Kept in your Mac's Keychain. Change or remove it in Settings ▸ AI redraw.</p>
            {error && (
              <p role="alert" className="text-[12px] text-destructive">
                {error}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <Dialog.Close asChild>
                <button type="button" className="mac-button">
                  Cancel
                </button>
              </Dialog.Close>
              <button type="submit" className="mac-primary h-8 w-auto px-4" disabled={!key.trim() || saving}>
                Save Key and Redraw
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
