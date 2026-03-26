"""Logging utilities for the AI Wars pipeline."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()

# Ensure log directory exists
_LOG_DIR = Path(__file__).parent.parent / "output" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
_log_file = _LOG_DIR / f"pipeline_{_timestamp}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file),
    ],
)
logger = logging.getLogger("ai_wars")

# Stage timing tracker
_stage_results: list[dict[str, Any]] = []


def log_api_call(endpoint: str, status_code: int, duration_seconds: float) -> None:
    """Log an API call with endpoint, status, and duration."""
    msg = f"API CALL | {endpoint} | status={status_code} | duration={duration_seconds:.2f}s"
    logger.info(msg)


def log_token_usage(model: str, input_tokens: int, output_tokens: int) -> None:
    """Log Anthropic token usage and estimated cost."""
    # Rough pricing (per 1M tokens, as of early 2025)
    costs: dict[str, tuple[float, float]] = {
        "claude-opus-4-5": (15.0, 75.0),
        "claude-haiku-4-5-20251001": (0.25, 1.25),
    }
    in_cost_per_m, out_cost_per_m = costs.get(model, (3.0, 15.0))
    est_cost = (input_tokens / 1_000_000) * in_cost_per_m + (output_tokens / 1_000_000) * out_cost_per_m
    msg = (
        f"TOKEN USAGE | model={model} | in={input_tokens} | out={output_tokens} "
        f"| est_cost=${est_cost:.4f}"
    )
    logger.info(msg)


class StageTimer:
    """Context manager that records stage name, duration, and status."""

    def __init__(self, stage_name: str) -> None:
        self.stage_name = stage_name
        self._start: float = 0.0

    def __enter__(self) -> "StageTimer":
        self._start = time.time()
        logger.info(f"STAGE START | {self.stage_name}")
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        duration = time.time() - self._start
        status = "ERROR" if exc_type else "OK"
        logger.info(f"STAGE END | {self.stage_name} | duration={duration:.1f}s | status={status}")
        _stage_results.append(
            {"stage": self.stage_name, "duration": duration, "status": status}
        )


def _format_duration(seconds: float) -> str:
    """Format seconds into a human-readable string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m {secs:.0f}s"


def print_summary() -> None:
    """Print a rich summary table of all pipeline stages."""
    table = Table(title="Pipeline Summary", show_header=True, header_style="bold magenta")
    table.add_column("Stage", style="cyan", width=20)
    table.add_column("Duration", justify="right", width=10)
    table.add_column("Status", justify="center", width=8)

    for row in _stage_results:
        status_style = "green" if row["status"] == "OK" else "yellow" if row["status"] == "DRAFT" else "red"
        table.add_row(
            row["stage"],
            _format_duration(row["duration"]),
            f"[{status_style}]{row['status']}[/{status_style}]",
        )

    console.print(table)
    logger.info(f"Pipeline complete. Log saved to: {_log_file}")
