import { useCallback, useEffect, useRef, useState } from "react";

import { uploadImage } from "@/lib/api";
import { hashFile } from "@/lib/hash";

/** What the browser knows about the file the moment it is dropped. */
export interface LocalPreview {
  file: File;
  previewUrl: string;
  /** 0 until the browser has decoded the image header. */
  width: number;
  height: number;
}

export interface UploadedImage extends LocalPreview {
  hash: string;
  imageId: string;
  format: string;
}

export interface UploadState {
  /** Set synchronously on drop so the workspace can open before the round-trip. */
  preview: LocalPreview | null;
  image: UploadedImage | null;
  uploading: boolean;
  error: Error | null;
  upload: (file: File) => Promise<UploadedImage | null>;
  /** Re-send the current file (after `image_expired`) and return the fresh id. */
  reupload: () => Promise<string | null>;
  /** Re-run the last upload from scratch, for the canvas's retry. */
  retry: () => void;
  clear: () => void;
}

/** Decode just enough of the file to size the canvas; resolves null if it can't. */
function probeSize(url: string): Promise<{ width: number; height: number } | null> {
  return new Promise((resolve) => {
    if (typeof Image === "undefined") {
      resolve(null);
      return;
    }
    const img = new Image();
    img.onload = () => resolve({ width: img.naturalWidth, height: img.naturalHeight });
    img.onerror = () => resolve(null);
    img.src = url;
  });
}

export function useUpload(): UploadState {
  const [preview, setPreview] = useState<LocalPreview | null>(null);
  const [image, setImage] = useState<UploadedImage | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const controller = useRef<AbortController | null>(null);
  const live = useRef<string | null>(null); // the object URL we own
  const last = useRef<File | null>(null);

  useEffect(() => {
    live.current = preview?.previewUrl ?? null;
  }, [preview]);

  useEffect(
    () => () => {
      controller.current?.abort();
      if (live.current) URL.revokeObjectURL(live.current);
    },
    [],
  );

  const clear = useCallback(() => {
    controller.current?.abort();
    setPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev.previewUrl);
      return null;
    });
    setImage(null);
    setError(null);
    setUploading(false);
    last.current = null;
  }, []);

  const upload = useCallback(async (file: File) => {
    controller.current?.abort();
    const ctl = new AbortController();
    controller.current = ctl;
    last.current = file;

    // Show the image first, ask the server second: the round-trip can take a
    // cold-start minute and a still page reads as a hung one.
    const previewUrl = URL.createObjectURL(file);
    setPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev.previewUrl);
      return { file, previewUrl, width: 0, height: 0 };
    });
    setImage(null);
    setUploading(true);
    setError(null);
    void probeSize(previewUrl).then((size) => {
      if (size) setPreview((p) => (p?.previewUrl === previewUrl ? { ...p, ...size } : p));
    });

    try {
      const [hash, res] = await Promise.all([hashFile(file), uploadImage(file, ctl.signal)]);
      if (ctl.signal.aborted) return null;
      const next: UploadedImage = { file, previewUrl, width: res.width, height: res.height, hash, imageId: res.image_id, format: res.format };
      setPreview((p) => (p?.previewUrl === previewUrl ? { ...p, width: res.width, height: res.height } : p));
      setImage(next);
      return next;
    } catch (err) {
      if ((err as Error).name === "AbortError") return null;
      setError(err as Error);
      return null;
    } finally {
      if (controller.current === ctl) setUploading(false);
    }
  }, []);

  const retry = useCallback(() => {
    if (last.current) void upload(last.current);
  }, [upload]);

  const reupload = useCallback(async () => {
    const img = image;
    if (!img) return null;
    const res = await uploadImage(img.file);
    setImage({ ...img, imageId: res.image_id });
    return res.image_id;
  }, [image]);

  return { preview, image, uploading, error, upload, reupload, retry, clear };
}
