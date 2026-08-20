/** Authenticated SSE via fetch (EventSource cannot send Authorization). */
export async function openAuthedEventStream(
  url: string,
  headers: Record<string, string>,
  onEvent: (eventType: string, data: string) => void,
): Promise<() => void> {
  const controller = new AbortController();
  void (async () => {
    try {
      const res = await fetch(url, {
        headers: { Accept: "text/event-stream", ...headers },
        signal: controller.signal,
      });
      if (!res.ok || !res.body) return;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let eventType = "message";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n");
        buffer = parts.pop() || "";
        for (const line of parts) {
          if (line.startsWith("event:")) eventType = line.slice(6).trim();
          else if (line.startsWith("data:")) {
            onEvent(eventType, line.slice(5).trim());
            eventType = "message";
          }
        }
      }
    } catch {
      /* aborted or network */
    }
  })();
  return () => controller.abort();
}
