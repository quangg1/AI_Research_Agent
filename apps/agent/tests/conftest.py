import os
import sys
from pathlib import Path

os.environ["LLM_BYOK_REQUIRED"] = "false"
os.environ["GOOGLE_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["XAI_API_KEY"] = ""
os.environ["GROK_API_KEY"] = ""
os.environ.setdefault("APP_ENV", "test")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
