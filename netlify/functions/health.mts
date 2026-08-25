import type { Config } from "@netlify/functions";

import { MAX_INPUT_CHARS } from "./_lib/http.mts";

export default async () =>
  Response.json({
    status: "ok",
    runtime: "netlify-functions",
    engine: "edge",
    // No ffmpeg in a serverless function, so mp3 is the only container.
    formats: ["mp3"],
    max_input_chars: MAX_INPUT_CHARS,
    auth_required: Boolean(Netlify.env.get("TTSKIT_API_KEY")),
  });

export const config: Config = {
  path: ["/healthz", "/health"],
};
