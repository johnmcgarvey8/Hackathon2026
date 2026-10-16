import json

from geo_agent.contracts import Brief
from geo_agent.evidence_assessment import BrandDefinition


SCHEMA_TYPES = (
    "Product",
    "ProductGroup",
    "Offer",
    "Brand",
    "Organization",
    "WebPage",
    "Article",
    "HowTo",
    "FAQPage",
    "ItemList",
    "BreadcrumbList",
    "Review",
    "AggregateRating",
    "LocalBusiness",
    "Store",
    "Event",
    "VideoObject",
    "ImageObject",
    "DefinedTerm",
)


def render_jsonld(
    schema_type: str,
    required_properties: tuple[str, ...],
    recommended_properties: tuple[str, ...],
    brand: BrandDefinition | None,
    brief: Brief,
) -> str:
    if schema_type not in SCHEMA_TYPES:
        raise ValueError("Schema type is not allowed")
    payload: dict[str, object] = {
        "@context": "https://schema.org",
        "@type": schema_type,
    }
    for property_name in dict.fromkeys((*required_properties, *recommended_properties)):
        if property_name == "url":
            payload[property_name] = str(brief.url)
        elif property_name == "name" and brand is not None:
            payload[property_name] = brand.name
        else:
            payload[property_name] = f"[VERIFY: {property_name}]"
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2)
