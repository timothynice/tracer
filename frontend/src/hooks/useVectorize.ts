import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import type { UploadedImage } from "./useUpload";
import { ApiError, vectorize, type EngineResult, type ParamValues, type VectorizeResponse } from "@/lib/api";
import { paramsKey } from "@/lib/schema";

export interface UseVectorizeArgs {
  image: UploadedImage | null;
  engines: string[];
  params: Record<string, ParamValues>;
  enabled?: boolean;
  /** Called when the server no longer has the upload; must return a fresh image_id. */
  reupload: () => Promise<string | null>;
  debounceMs?: number;
}

export interface VectorizeState {
  results: Record<string, EngineResult> | undefined;
  data: VectorizeResponse | undefined;
  /** A request is in flight (the displayed results may be from the previous settings). */
  updating: boolean;
  /** The displayed results belong to an earlier key. */
  stale: boolean;
  error: Error | null;
  refetch: () => void;
}

/** Debounces parameter changes, cancels superseded requests, caches per (image, engines, params). */
export function useVectorize({ image, engines, params, enabled = true, reupload, debounceMs = 250 }: UseVectorizeArgs): VectorizeState {
  const engineList = useMemo(() => [...engines].sort(), [engines]);
  const liveKey = useMemo(
    () => (image ? { hash: image.hash, engines: engineList.join(","), params: Object.fromEntries(engineList.map((e) => [e, paramsKey(params[e] ?? {})])) } : null),
    [image, engineList, params],
  );
  const liveKeyString = JSON.stringify(liveKey);

  // Debounce: the query only sees the key after it has been stable for debounceMs.
  const [settled, setSettled] = useState(liveKeyString);
  useEffect(() => {
    if (settled === liveKeyString) return;
    const t = setTimeout(() => setSettled(liveKeyString), debounceMs);
    return () => clearTimeout(t);
  }, [liveKeyString, settled, debounceMs]);
  const settledKey = useMemo(() => JSON.parse(settled) as typeof liveKey, [settled]);

  const query = useQuery({
    queryKey: ["vectorize", settledKey],
    enabled: enabled && !!image && !!settledKey && engineList.length > 0 && settledKey.hash === image?.hash,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
    gcTime: 10 * 60 * 1000,
    queryFn: async ({ signal }) => {
      if (!image) throw new Error("no image");
      const hash = image.hash;
      const parameters = Object.fromEntries(engineList.map((e) => [e, params[e] ?? {}]));
      try {
        return { ...(await vectorize({ imageId: image.imageId, engines: engineList, parameters }, signal)), hash };
      } catch (err) {
        if (err instanceof ApiError && err.code === "image_expired") {
          const fresh = await reupload();
          if (!fresh) throw err;
          return { ...(await vectorize({ imageId: fresh, engines: engineList, parameters }, signal)), hash };
        }
        throw err;
      }
    },
  });

  // keepPreviousData holds the last result across key changes, which is what we
  // want while parameters are being dragged — but a result from a *different*
  // image is a picture of something else, so it is dropped.
  const data = query.data?.hash === image?.hash ? query.data : undefined;

  return {
    results: data?.results,
    data,
    updating: query.isFetching || settled !== liveKeyString,
    stale: query.isPlaceholderData || settled !== liveKeyString,
    error: query.error,
    refetch: () => void query.refetch(),
  };
}
