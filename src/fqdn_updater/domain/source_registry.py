from __future__ import annotations

from fqdn_updater.domain.config_schema import ServiceDefinitionConfig, ServiceSourceConfig

_BASE_URL = "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main"

_SERVICE_KEYS: tuple[str, ...] = (
    "news",
    "cloudflare",
    "cloudfront",
    "digitalocean",
    "discord",
    "google_ai",
    "google_meet",
    "google_play",
    "hdrezka",
    "hetzner",
    "meta",
    "ovh",
    "roblox",
    "telegram",
    "tiktok",
    "twitter",
    "youtube",
)

_SERVICE_DESCRIPTIONS: dict[str, str] = {
    "news": "Blocked news domains from itdoginfo/allow-domains.",
    "cloudflare": "Cloudflare domains and subnets from itdoginfo/allow-domains.",
    "cloudfront": "CloudFront domains and subnets from itdoginfo/allow-domains.",
    "digitalocean": "DigitalOcean domains and subnets from itdoginfo/allow-domains.",
    "discord": "Discord domains and subnets from itdoginfo/allow-domains.",
    "google_ai": "Dedicated Google AI domains from itdoginfo/allow-domains.",
    "google_meet": "Google Meet domains and subnets from itdoginfo/allow-domains.",
    "google_play": "Google Play domains from itdoginfo/allow-domains.",
    "hdrezka": "HDRezka domains from itdoginfo/allow-domains.",
    "hetzner": "Hetzner domains and subnets from itdoginfo/allow-domains.",
    "meta": "Meta family domains and subnets from itdoginfo/allow-domains.",
    "ovh": "OVH domains and subnets from itdoginfo/allow-domains.",
    "roblox": "Roblox domains and subnets from itdoginfo/allow-domains.",
    "telegram": "Telegram domains and subnets from itdoginfo/allow-domains.",
    "tiktok": "TikTok domains from itdoginfo/allow-domains.",
    "twitter": "Twitter and X domains and subnets from itdoginfo/allow-domains.",
    "youtube": "YouTube domains from itdoginfo/allow-domains.",
}

_SUBNET_SERVICE_KEYS: frozenset[str] = frozenset(
    {
        "cloudflare",
        "cloudfront",
        "digitalocean",
        "discord",
        "google_meet",
        "hetzner",
        "meta",
        "ovh",
        "roblox",
        "telegram",
        "twitter",
    }
)


def _service_definition(key: str) -> ServiceDefinitionConfig:
    if key == "news":
        return ServiceDefinitionConfig(
            key=key,
            source_urls=[f"{_BASE_URL}/Categories/news.lst"],
            format="raw_domain_list",
            description=_SERVICE_DESCRIPTIONS[key],
        )

    sources = [
        ServiceSourceConfig(
            url=f"{_BASE_URL}/Services/{key}.lst",
            format="raw_domain_list",
        )
    ]
    if key in _SUBNET_SERVICE_KEYS:
        sources.extend(
            (
                ServiceSourceConfig(
                    url=f"{_BASE_URL}/Subnets/IPv4/{key}.lst",
                    format="raw_cidr_list",
                ),
                ServiceSourceConfig(
                    url=f"{_BASE_URL}/Subnets/IPv6/{key}.lst",
                    format="raw_cidr_list",
                ),
            )
        )

    return ServiceDefinitionConfig(
        key=key,
        sources=sources,
        description=_SERVICE_DESCRIPTIONS[key],
    )


def builtin_service_definitions() -> list[ServiceDefinitionConfig]:
    """Return built-in source definitions used for scaffold config generation."""

    return [_service_definition(key) for key in _SERVICE_KEYS]
