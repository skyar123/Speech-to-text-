import type { Config } from "@netlify/functions";

import { MODEL_IDS } from "./_lib/http.mts";

/** `GET /v1/models` — present so OpenAI clients that probe for models work. */
export default async () =>
  Response.json(
    {
      object: "list",
      data: MODEL_IDS.map((id) => ({ id, object: "model", created: 0, owned_by: "ttskit" })),
    },
    { headers: { "Cache-Control": "public, max-age=3600" } },
  );

export const config: Config = {
  path: ["/v1/models", "/models"],
};
