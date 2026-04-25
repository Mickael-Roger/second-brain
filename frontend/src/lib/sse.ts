// Minimal SSE-over-fetch client. The browser's EventSource doesn't support POST,
// so we parse the text/event-stream stream manually.

export interface ServerEvent {
  event: string;
  data: unknown;
}

export interface StreamOptions {
  signal?: AbortSignal;
  onEvent: (ev: ServerEvent) => void;
}

export async function streamSse(
  path: string,
  body: unknown,
  { signal, onEvent }: StreamOptions,
): Promise<void> {
  const resp = await fetch(path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });

  if (!resp.ok || !resp.body) {
    let detail = "";
    try {
      detail = await resp.text();
    } catch {
      // ignore
    }
    throw new Error(`SSE request failed (${resp.status}): ${detail}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const ev = parseChunk(chunk);
      if (ev) onEvent(ev);
    }
  }
}

function parseChunk(chunk: string): ServerEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of chunk.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (dataLines.length === 0) return null;
  const raw = dataLines.join("\n");
  try {
    return { event, data: JSON.parse(raw) };
  } catch {
    return { event, data: raw };
  }
}
