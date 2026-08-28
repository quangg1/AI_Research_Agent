const STORAGE_KEY = "kiln_notify_enabled";

export function notificationsSupported() {
  return typeof window !== "undefined" && "Notification" in window;
}

export function notificationsEnabled() {
  if (!notificationsSupported()) return false;
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export async function requestNotificationPermission(): Promise<boolean> {
  if (!notificationsSupported()) return false;
  if (Notification.permission === "granted") {
    try {
      localStorage.setItem(STORAGE_KEY, "1");
    } catch {
      /* ignore */
    }
    return true;
  }
  if (Notification.permission === "denied") return false;
  const result = await Notification.requestPermission();
  const ok = result === "granted";
  try {
    localStorage.setItem(STORAGE_KEY, ok ? "1" : "0");
  } catch {
    /* ignore */
  }
  return ok;
}

export function notifyResearch(title: string, body: string, runId?: string) {
  if (!notificationsSupported() || Notification.permission !== "granted") return;
  try {
    if (localStorage.getItem(STORAGE_KEY) !== "1") return;
  } catch {
    return;
  }
  const n = new Notification(title, {
    body,
    tag: runId ? `kiln-run-${runId}` : "kiln-research",
    icon: "/favicon.ico",
  });
  n.onclick = () => {
    window.focus();
    if (runId) window.location.href = `/?run=${runId}`;
    n.close();
  };
}
