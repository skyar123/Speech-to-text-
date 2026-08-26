/** Shared request handling: auth, OpenAI-shaped errors, input limits. */

/**
 * Ceiling on a *single* request. This is not a style choice — a serverless
 * function has a wall-clock timeout and Netlify caps a synchronous response at
 * 6 MB. Edge audio runs ~377 bytes per character, so 6 MB lands near 15,900
 * characters; 12,000 keeps a comfortable margin on both limits.
 *
 * It is not a limit on document length. The playground splits long documents
 * client-side and stitches the pieces, so nothing there is capped — and
 * `ttskit book` has no limit at all.
 */
export const MAX_INPUT_CHARS = Number(Netlify.env.get("TTSKIT_MAX_INPUT_CHARS") ?? 12000);

export const MODEL_IDS = ["tts-1", "tts-1-hd", "gpt-4o-mini-tts", "ttskit"];

export function errorResponse(
  status: number,
  message: string,
  { param, code, type }: { param?: string; code?: string; type?: string } = {},
): Response {
  return Response.json(
    {
      error: {
        message,
        type: type ?? (status < 500 ? "invalid_request_error" : "api_error"),
        param: param ?? null,
        code: code ?? null,
      },
    },
    { status },
  );
}

/**
 * Enforce `TTSKIT_API_KEY` when it is set. When it is unset the endpoint is
 * open — convenient for a personal demo, but see the deploy notes: an open
 * endpoint is a standing invitation to burn your function minutes.
 */
export function checkAuth(req: Request): Response | null {
  const required = Netlify.env.get("TTSKIT_API_KEY");
  if (!required) return null;
  const presented = (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "").trim();
  if (presented !== required) {
    return errorResponse(401, "Invalid API key", { code: "invalid_api_key" });
  }
  return null;
}

export function checkLength(text: string): Response | null {
  if (text.length <= MAX_INPUT_CHARS) return null;
  return errorResponse(
    413,
    `Input is ${text.length} characters; a single request to this endpoint accepts ` +
      `${MAX_INPUT_CHARS} (a serverless response is capped at 6 MB). Split the text and ` +
      "concatenate the MP3s — they join cleanly — or use the playground, which does that " +
      "for you, or `ttskit book` locally, which has no limit.",
    { param: "input", code: "input_too_long" },
  );
}
