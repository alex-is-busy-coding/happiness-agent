"""Settings: where things live, which model staffs the desk, and the limits on a run."""

import os
from pathlib import Path

from agents import ModelSettings
from agents.extensions.models.litellm_model import LitellmModel
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "happiness.duckdb"
STORIES_DIR = ROOT / "stories"

# LiteLLM routes to Gemini on Vertex AI, which authenticates with gcloud credentials: no API key.
# Locally the project comes from GOOGLE_CLOUD_PROJECT; on Cloud Run the metadata server supplies it.
MODEL_NAME = "vertex_ai/gemini-3.5-flash"
VERTEX = {"vertex_location": "global", "vertex_project": os.environ.get("GOOGLE_CLOUD_PROJECT")}
MODEL = LitellmModel(model=MODEL_NAME, api_key="unused")
# A stalled model call would hang the page: give up after 90 s. Retry with backoff, because
# Vertex answers a burst of parallel researchers with 429s that clear in a few seconds.
MODEL_SETTINGS = ModelSettings(
    extra_args={**VERTEX, "timeout": 90, "num_retries": 3, "retry_strategy": "exponential_backoff_retry"}
)

# One run. The prompts ask for restraint; these enforce it.
MAX_RESEARCHERS = 6
QUERY_BUDGET = 6  # successful queries per researcher
MAX_ROWS = 50  # rows of a query result the model gets to see
LEAD_MAX_TURNS = 12
RESEARCHER_MAX_TURNS = 14
# Graders wait five minutes for an answer. No researcher starts after the cutoff, and the
# research is cut off at the deadline, which leaves the writer a minute and a half.
NEW_RESEARCHER_CUTOFF = 150  # seconds into the run
RESEARCH_DEADLINE = 210

# The whole service. Every run spends model calls someone pays for.
MAX_CONCURRENT_RUNS = int(os.environ.get("MAX_CONCURRENT_RUNS", "4"))
MAX_RUNS_PER_DAY = int(os.environ.get("MAX_RUNS_PER_DAY", "200"))
