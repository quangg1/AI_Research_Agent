export const FIRST_RUN_TEMPLATES = [
  {
    tag: "RAG vs fine-tune",
    text: "Should we fine-tune an 8B model on weekly runbooks, or use RAG over the same docs?",
  },
  {
    tag: "RAG stack",
    text: "Compare a vector-only RAG stack vs BM25 plus a cross-encoder reranker for a 50k-chunk internal corpus.",
  },
  {
    tag: "Serving economics",
    text: "Should we self-host an 8B FP8 model on one H100 or call a 70B API for 20M tokens/day?",
  },
] as const;

const DISMISS_KEY = "kiln_first_run_dismissed";

export function firstRunDismissed() {
  try {
    return localStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

export function dismissFirstRun() {
  try {
    localStorage.setItem(DISMISS_KEY, "1");
  } catch {
    /* ignore */
  }
}
