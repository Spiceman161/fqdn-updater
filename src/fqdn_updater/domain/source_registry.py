from __future__ import annotations

from fqdn_updater.domain.config_schema import ServiceDefinitionConfig

_BASE_URL = "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main"

_BUILTIN_SERVICES: tuple[ServiceDefinitionConfig, ...] = (
    ServiceDefinitionConfig(
        key="news",
        source_urls=[f"{_BASE_URL}/Categories/news.lst"],
        format="raw_domain_list",
        description="Blocked news domains from itdoginfo/allow-domains.",
    ),
    ServiceDefinitionConfig(
        key="hdrezka",
        source_urls=[f"{_BASE_URL}/Services/hdrezka.lst"],
        format="raw_domain_list",
        description="HDRezka domains from itdoginfo/allow-domains.",
    ),
    ServiceDefinitionConfig(
        key="meta",
        source_urls=[f"{_BASE_URL}/Services/meta.lst"],
        format="raw_domain_list",
        description="Meta family domains from itdoginfo/allow-domains.",
    ),
    ServiceDefinitionConfig(
        key="tiktok",
        source_urls=[f"{_BASE_URL}/Services/tiktok.lst"],
        format="raw_domain_list",
        description="TikTok domains from itdoginfo/allow-domains.",
    ),
    ServiceDefinitionConfig(
        key="twitter",
        source_urls=[f"{_BASE_URL}/Services/twitter.lst"],
        format="raw_domain_list",
        description="Twitter and X domains from itdoginfo/allow-domains.",
    ),
    ServiceDefinitionConfig(
        key="youtube",
        source_urls=[f"{_BASE_URL}/Services/youtube.lst"],
        format="raw_domain_list",
        description="YouTube domains from itdoginfo/allow-domains.",
    ),
    ServiceDefinitionConfig(
        key="discord",
        source_urls=[f"{_BASE_URL}/Services/discord.lst"],
        format="raw_domain_list",
        description="Discord domains from itdoginfo/allow-domains.",
    ),
    ServiceDefinitionConfig(
        key="cloudflare",
        source_urls=[f"{_BASE_URL}/Subnets/IPv4/cloudflare.lst"],
        format="raw_cidr_list",
        description="Cloudflare IPv4 ranges from itdoginfo/allow-domains.",
    ),
    ServiceDefinitionConfig(
        key="telegram",
        source_urls=[f"{_BASE_URL}/Services/telegram.lst"],
        format="raw_domain_list",
        description="Telegram domains from itdoginfo/allow-domains.",
    ),
    ServiceDefinitionConfig(
        key="google_meet",
        source_urls=[f"{_BASE_URL}/Subnets/IPv4/google_meet.lst"],
        format="raw_cidr_list",
        description="Google Meet IPv4 ranges from itdoginfo/allow-domains.",
    ),
    ServiceDefinitionConfig(
        key="google_ai",
        source_urls=[f"{_BASE_URL}/Services/google_ai.lst"],
        format="raw_domain_list",
        description="Dedicated Google AI domains from itdoginfo/allow-domains.",
    ),
)


def builtin_service_definitions() -> list[ServiceDefinitionConfig]:
    """Return built-in source definitions used for scaffold config generation."""

    return [service.model_copy(deep=True) for service in _BUILTIN_SERVICES]
