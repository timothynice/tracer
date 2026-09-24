import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { UploadedImage } from "./useUpload";
import { ApiError, vectorize, type EngineResult, type ParamValues, type VectorizeResponse } from "@/lib/api";
import { paramsKey } from "@/lib/schema";

export interface UseVectorizeArgs {
  image: UploadedImage | null;
  engines: string[];
  params: Record<string, ParamValues>;
  /** Let the server trace every Auto candidate and pick one; `params` then play no part. */
  auto?: boolean;
  enabled?: boolean;
  /** Called when the server no longer has the upload; must return a fresh image_id. */
  reupload: () => Promise<string | null>;
  debounceMs?: number;
}

export interface VectorizeState {
  results: Record<string, EngineResult> | undefined;
  data: (VectorizeResponse & { hash: string }) | undefined;
  /** A request is in flight (the displayed results may be from the previous settings). */
  updating: boolean;
  /** The displayed results belong to an earlier key. */
  stale: boolean;
  error: Error | null;
  refetch: () => void;
  /**
   * Store `response` as the answer for this image traced with `params`, so that
   * asking for those parameters later shows it at once instead of tracing
   * again (each Auto candidate is a trace some preset would have asked for).
   * An answer already cached is kept.
   */
  seed: (params: Record<string, ParamValues>, response: VectorizeResponse) => void;
}

type Key =
  | { hash: string; engines: string; auto: true }
  | { hash: string; engines: string; params: Record<string, string> };

function paramsQueryKey(hash: string, engines: string[], params: Record<string, ParamValues>): Key {
  return { hash, engines: engines.join(","), params: Object.fromEntries(engines.map((e) => [e, paramsKey(params[e] ?? {})])) };
}

/** Debounces parameter changes, cancels superseded requests, caches per (image, engines, params | auto). */
export function useVectorize({ image, engines, params, auto = false, enabled = true, reupload, debounceMs = 250 }: UseVectorizeArgs): VectorizeState {
  const client = useQueryClient();
  const engineList = useMemo(() => [...engines].sort(), [engines]);
  const liveKey = useMemo<Key | null>(
    () =>
      image
        ? auto
          ? { hash: image.hash, engines: engineList.join(","), auto: true }
          : paramsQueryKey(image.hash, engineList, params)
        : null,
    [image, engineList, params, auto],
  );
  const liveKeyString = JSON.stringify(liveKey);

  // Debounce: the query only sees the key after it has been stable for
  // debounceMs — unless its answer is already cached (Auto, or settings seen
  // before), which is shown at once: there is nothing to wait for.
  const [debounced, setSettled] = useState(liveKeyString);
  const liveCached = liveKey !== null && client.getQueryData(["vectorize", liveKey]) !== undefined;
  const settled = liveCached ? liveKeyString : debounced;
  useEffect(() => {
    if (debounced === liveKeyString) return;
    if (liveCached) {
      setSettled(liveKeyString);
      return;
    }
    const t = setTimeout(() => setSettled(liveKeyString), debounceMs);
    return () => clearTimeout(t);
  }, [liveKeyString, debounced, debounceMs, liveCached]);
  const settledKey = useMemo(() => JSON.parse(settled) as Key | null, [settled]);
  const settledAuto = !!(settledKey && "auto" in settledKey && settledKey.auto);

  const query = useQuery({
    queryKey: ["vectorize", settledKey],
    enabled: enabled && !!image && !!settledKey && engineList.length > 0 && settledKey.hash === image?.hash,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
    gcTime: 10 * 60 * 1000,
    queryFn: async ({ signal }) => {
      if (!image) throw new Error("no image");
      const hash = image.hash;
      // Auto's candidates carry their own parameters; what the panel holds is not sent.
      const parameters = settledAuto ? {} : Object.fromEntries(engineList.map((e) => [e, params[e] ?? {}]));
      const args = { engines: engineList, parameters, auto: settledAuto };
      try {
        return { ...(await vectorize({ imageId: image.imageId, ...args }, signal)), hash };
      } catch (err) {
        if (err instanceof ApiError && err.code === "image_expired") {
          const fresh = await reupload();
          if (!fresh) throw err;
          return { ...(await vectorize({ imageId: fresh, ...args }, signal)), hash };
        }
        throw err;
      }
    },
  });

  // keepPreviousData holds the last result across key changes, which is what we
  // want while parameters are being dragged — but a result from a *different*
  // image is a picture of something else, so it is dropped.
  const data = query.data?.hash === image?.hash ? query.data : undefined;

  const hash = image?.hash;
  const seed = useCallback(
    (forParams: Record<string, ParamValues>, response: VectorizeResponse) => {
      if (!hash) return;
      const key = ["vectorize", paramsQueryKey(hash, engineList, forParams)];
      if (client.getQueryData(key) === undefined) client.setQueryData(key, { ...response, hash });
    },
    [client, hash, engineList],
  );

  return {
    results: data?.results,
    data,
    updating: query.isFetching || settled !== liveKeyString,
    stale: query.isPlaceholderData || settled !== liveKeyString,
    error: query.error,
    refetch: () => void query.refetch(),
    seed,
  };
}
