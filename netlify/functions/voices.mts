import type { Config } from "@netlify/functions";

import { listVoices } from "./_lib/edge-tts.mts";
import { errorResponse } from "./_lib/http.mts";
import { OPENAI_VOICES, resolveVoice } from "./_lib/voices.mts";

/** `GET /v1/voices` — the full Edge catalogue plus the alias table. */
export default async (req: Request) => {
  const locale = new URL(req.url).searchParams.get("locale");

  try {
    let voices = await listVoices();
    if (locale) {
      const needle = locale.toLowerCase();
      voices = voices.filter((voice) => voice.locale.toLowerCase().startsWith(needle));
    }

    return Response.json(
      {
        engine: "edge",
        openai_aliases: Object.fromEntries(
          OPENAI_VOICES.map((name) => [name, resolveVoice(name)]),
        ),
        voices,
      },
      // The catalogue changes rarely; let the CDN carry the load.
      { headers: { "Cache-Control": "public, max-age=3600, s-maxage=86400" } },
    );
  } catch (error) {
    return errorResponse(502, `Could not list voices: ${(error as Error).message}`, {
      type: "api_error",
    });
  }
};

export const config: Config = {
  path: ["/v1/voices", "/voices"],
};
