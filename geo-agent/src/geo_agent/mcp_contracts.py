from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from geo_agent.contracts import EvidenceQuote, QueryPair
from geo_agent.evidence_assessment import BrandAlias, BrandDefinition


UUID_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
Identifier = Annotated[str, Field(pattern=UUID_PATTERN)]
Revision = Annotated[int, Field(ge=1)]
DefinitionVersion = Annotated[int, Field(ge=0)]
IdempotencyKey = Annotated[str, Field(min_length=1, max_length=200)]
InputHash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Cursor = Annotated[str, Field(max_length=4096)]
BriefText = Annotated[str, Field(min_length=1, max_length=1000)]
Locale = Annotated[str, Field(pattern=r"^[a-z]{2}-[A-Z]{2}$")]
PublicUrl = Annotated[str, Field(min_length=1, max_length=2000)]
QueryId = Annotated[str, Field(pattern=r"^q-[1-5]$")]
EvidenceId = Annotated[str, Field(pattern=r"^[a-z0-9-]+$")]
ProfileId = Annotated[
    str,
    Field(pattern=r"^(chatgpt-style|claude-backed|copilot-style)$"),
]
class MCPInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MCPQueryEvidence(MCPInput):
    evidence_id: str = Field(pattern=r"^[a-z0-9-]+$")
    quote: str = Field(min_length=1, max_length=500)


class MCPQueryPair(MCPInput):
    query_id: str = Field(pattern=r"^q-[1-5]$")
    priority: int = Field(ge=1, le=5)
    rationale: str = Field(min_length=1, max_length=500)
    intent: str = Field(min_length=1, max_length=500)
    branded: bool = False
    chat_query: str = Field(min_length=1, max_length=500)
    grounding_query: str = Field(min_length=1, max_length=500)
    evidence: list[MCPQueryEvidence] = Field(min_length=1, max_length=2)

    def to_domain(self) -> QueryPair:
        return QueryPair(
            query_id=self.query_id,
            priority=self.priority,
            rationale=self.rationale,
            intent=self.intent,
            branded=self.branded,
            chat_query=self.chat_query,
            grounding_query=self.grounding_query,
            evidence=tuple(
                EvidenceQuote(evidence_id=item.evidence_id, quote=item.quote)
                for item in self.evidence
            ),
        )


class MCPBrandAlias(MCPInput):
    text: str = Field(min_length=1, max_length=120)
    ambiguous: bool = False


QueryPairs = Annotated[list[MCPQueryPair], Field(min_length=5, max_length=5)]
BrandAliases = Annotated[list[MCPBrandAlias], Field(max_length=10)]
BrandDomains = Annotated[list[str], Field(max_length=10)]


def brand_definition(
    name: str,
    aliases: list[MCPBrandAlias],
    domains: list[str],
) -> BrandDefinition:
    return BrandDefinition(
        name=name,
        aliases=tuple(
            BrandAlias(text=alias.text, ambiguous=alias.ambiguous)
            for alias in aliases
        ),
        domains=tuple(domains),
    )
