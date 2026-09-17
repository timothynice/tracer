import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll } from "vitest";

import { API_URL, ApiError, getEngines, getHealth, uploadImage, vectorize } from "./api";
import { server } from "@/test/server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("health and engines parse", async () => {
  expect((await getHealth()).engines).toEqual(["potrace", "vtracer"]);
  const engines = await getEngines();
  expect(engines[0].params.properties.threshold.ui?.control).toBe("slider");
});

test("upload and vectorize send the expected form fields", async () => {
  const up = await uploadImage(new File([new Uint8Array([1, 2, 3])], "x.png", { type: "image/png" }));
  expect(up.image_id).toHaveLength(32);
  const res = await vectorize({ imageId: up.image_id, engines: ["potrace"], parameters: { potrace: { threshold: 200 } } });
  expect(Object.keys(res.results)).toEqual(["potrace"]);
  expect(res.parameters_used.potrace.threshold).toBe(200);
});

test("structured error bodies become ApiError codes", async () => {
  server.use(http.post(`${API_URL}/vectorize`, () => HttpResponse.json({ detail: { code: "image_expired", message: "gone" } }, { status: 404 })));
  const err = await vectorize({ imageId: "x", engines: [], parameters: {} }).catch((e) => e);
  expect(err).toBeInstanceOf(ApiError);
  expect(err.code).toBe("image_expired");
  expect(err.status).toBe(404);
  expect(err.message).toBe("gone");
});

test("422 validation arrays become validation_error with a location", async () => {
  server.use(
    http.post(`${API_URL}/vectorize`, () =>
      HttpResponse.json({ detail: [{ loc: ["potrace", "alphamax"], msg: "Input should be less than or equal to 1.3334", type: "x" }] }, { status: 422 }),
    ),
  );
  const err = (await vectorize({ imageId: "x", engines: [], parameters: {} }).catch((e) => e)) as ApiError;
  expect(err.code).toBe("validation_error");
  expect(err.message).toMatch(/^potrace\.alphamax: /);
});

test("network failures become ApiError('network')", async () => {
  server.use(http.get(`${API_URL}/health`, () => HttpResponse.error()));
  const err = (await getHealth().catch((e) => e)) as ApiError;
  expect(err.code).toBe("network");
});
