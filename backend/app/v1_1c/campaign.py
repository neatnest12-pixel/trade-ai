"""V1.1C identity and paths only; no worker is defined or started here."""
from pathlib import Path

JOURNAL_PATH = Path("certified_data/FORWARD_PAPER_JOURNAL_V1_1C.jsonl")
WORKER_STATE_PATH = Path("runtime/v1_1c_worker_state.json")
LOCK_PATH = Path("runtime/v1_1c_forward_paper.lock")
