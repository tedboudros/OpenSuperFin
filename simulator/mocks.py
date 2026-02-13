"""Mock integrations for simulation mode.

In simulation, output adapters capture signals to files instead of sending
them to real destinations. Implements the OutputAdapter protocol.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.models.memos import InvestmentMemo
from core.models.signals import Signal
from core.protocols import DeliveryResult

logger = logging.getLogger(__name__)


class MockOutputAdapter:
    """Captures signals to files instead of sending them.

    Implements the OutputAdapter protocol. Used in simulation mode
    so no real notifications are sent during backtests.
    """

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._signal_count = 0

    @property
    def name(self) -> str:
        return "mock_output"

    async def send(self, signal: Signal, memo: InvestmentMemo | None = None) -> DeliveryResult:
        """Capture the signal to a file instead of sending it."""
        self._signal_count += 1

        filepath = self._output_dir / f"signal_{self._signal_count:04d}_{signal.ticker}.json"
        data = {
            "signal": signal.model_dump(mode="json"),
            "memo_summary": memo.executive_summary if memo else None,
        }

        filepath.write_text(json.dumps(data, indent=2))
        logger.debug("Mock captured signal: %s %s", signal.direction, signal.ticker)

        return DeliveryResult(success=True, adapter=self.name, message="Captured to file")

    @property
    def signal_count(self) -> int:
        return self._signal_count
