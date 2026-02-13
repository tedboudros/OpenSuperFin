"""InvestmentMemo model -- structured analysis documents produced by the AI."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


class Scenario(BaseModel):
    """A single scenario in the investment memo's scenario tree."""

    name: str
    probability: float = Field(ge=0.0, le=1.0)
    description: str
    target_price: float | None = None
    timeline: str | None = None


class InvestmentMemo(BaseModel):
    """A structured investment memo produced by the AI orchestrator.

    Stored on disk as Markdown files in memos/.
    """

    id: str = Field(default_factory=lambda: f"memo_{uuid4().hex[:12]}")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str = ""

    # Content sections
    executive_summary: str = ""
    catalyst: str = ""
    market_context: str = ""
    pricing_vs_view: str = ""
    scenario_tree: list[Scenario] = Field(default_factory=list)
    trade_expression: str = ""
    entry_plan: str = ""
    risks: list[str] = Field(default_factory=list)
    monitoring_plan: str = ""

    # Metadata
    agents_used: list[str] = Field(default_factory=list)
    model_provider: str = ""
    model_name: str = ""

    def to_markdown(self) -> str:
        """Render the memo as a Markdown document with YAML frontmatter."""
        lines = [
            "---",
            f"id: {self.id}",
            f"created_at: {self.created_at.isoformat()}",
            f"correlation_id: {self.correlation_id}",
            f"agents_used: [{', '.join(self.agents_used)}]",
            f"model_provider: {self.model_provider}",
            f"model_name: {self.model_name}",
            "---",
            "",
        ]

        if self.executive_summary:
            lines.extend(["# Executive Summary", "", self.executive_summary, ""])

        if self.catalyst:
            lines.extend(["## Catalyst", "", self.catalyst, ""])

        if self.market_context:
            lines.extend(["## Market Context", "", self.market_context, ""])

        if self.pricing_vs_view:
            lines.extend(["## Pricing vs View", "", self.pricing_vs_view, ""])

        if self.scenario_tree:
            lines.extend(["## Scenarios", ""])
            lines.append("| Scenario | Probability | Target | Timeline |")
            lines.append("|----------|-------------|--------|----------|")
            for s in self.scenario_tree:
                target = f"${s.target_price:,.2f}" if s.target_price else "—"
                timeline = s.timeline or "—"
                lines.append(f"| {s.name} | {s.probability:.0%} | {target} | {timeline} |")
            lines.append("")

        if self.trade_expression:
            lines.extend(["## Trade Expression", "", self.trade_expression, ""])

        if self.entry_plan:
            lines.extend(["## Entry Plan", "", self.entry_plan, ""])

        if self.risks:
            lines.extend(["## Risks", ""])
            for risk in self.risks:
                lines.append(f"- {risk}")
            lines.append("")

        if self.monitoring_plan:
            lines.extend(["## Monitoring Plan", "", self.monitoring_plan, ""])

        return "\n".join(lines)
