import type { Config, Context } from "@netlify/functions";

import { synthesize } from "./_lib/edge-tts.mts";
import { toSrt, toVtt } from "./_lib/subtitles.mts";
import { checkAuth, checkLength, errorResponse } from "./_lib/http.mts";
import { resolveVoice, speedToRate } from "./_lib/voices.mts";

/**
 * `POST /v1/audio/subtitles` — a ttskit extension, not part of the OpenAI API.
 *
 * Returns the voiceover plus matching captions in one round trip, built from
 * the engine's real word timings rather than from forced alignment.
 */
export default async (req: Request, context: Context) => {
  if (req.method !== "POST") {
    return errorResponse(405, "Use POST for /v1/audio/subtitles");
  }

  const unauthorized = checkAuth(req);
  if (unauthorized) return unauthorized;

  let body: Record<string, any>;
  try {
    body = await req.json();
  } catch {
    return errorResponse(400, "Request body must be valid JSON");
  }

  const input = typeof body.input === "string" ? body.input.trim() : "";
  if (!input) {
    return errorResponse(400, "'input' must be a non-empty string", { param: "input" });
  }

  const tooLong = checkLength(input);
  if (tooLong) return tooLong;

  const voice = resolveVoice(body.voice);
  const boundary = body.boundary === "sentence" ? "sentence" : "word";

  try {
    const result = await synthesize(input, {
      voice,
      rate: typeof body.rate === "string" ? body.rate : speedToRate(Number(body.speed ?? 1)),
      boundary,
    });

    return Response.json({
      voice,
      format: "mp3",
      // 48 kbps constant bitrate, so bytes map exactly onto duration.
      duration_ms: Math.round((result.audio.length / 6000) * 1000),
      audio_base64: result.audio.toString("base64"),
      srt: toSrt(result.boundaries),
      vtt: toVtt(result.boundaries),
      marks: result.boundaries.map((mark) => ({
        text: mark.text,
        start_ms: Math.round(mark.startMs * 100) / 100,
        end_ms: Math.round((mark.startMs + mark.durationMs) * 100) / 100,
        kind: mark.kind,
      })),
    });
  } catch (error) {
    console.error("subtitle synthesis failed", { requestId: context.requestId, error });
    return errorResponse(502, `Synthesis failed: ${(error as Error).message}`, {
      type: "api_error",
    });
  }
};

export const config: Config = {
  path: "/v1/audio/subtitles",
};
