from __future__ import annotations

from dataclasses import dataclass, field

BUILT_IN_ROLE_DEFINITIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "super_admin": ("Unrestricted platform administrator", ()),
    "admin": ("Day-to-day administrator with write access", ()),
    "analyst": ("Can run scans and view equity analytics", ()),
    "viewer": ("Read-only access to market data and insights", ()),
}

BUILT_IN_PERMISSION_DEFINITIONS: dict[str, str] = {
    "users.read": "List and view users",
    "users.write": "Create, update and manage users",
    "roles.read": "List and view roles",
    "roles.write": "Create, update and manage roles",
    "stocks.read": "View stock and market data",
    "stocks.write": "Create and manage stocks",
    "market_data.write": "Import and refresh market data",
    "scanners.read": "View scanners and scan results",
    "scanners.run": "Execute equity scanners",
    "analytics.read": "View analytics",
    "ai.read": "View AI-generated insights",
    "dashboard.read": "View the dashboard and scanner workspace",
    "portfolio.read": "View portfolios and holdings",
    "portfolio.write": "Create and update portfolios",
    "watchlists.read": "View watchlists",
    "watchlists.write": "Create and manage watchlists",
    "alerts.read": "View price alerts",
    "alerts.write": "Create and manage price alerts",
    "notifications.read": "View notification history and preferences",
    "notifications.write": "Manage notification channels and preferences",
    "pipeline.run": "Run the end-of-day data pipeline",
    "daily_summary.run": "Run the daily summary",
    "scheduler.run": "Run notification schedulers",
    "settings.read": "View application settings",
    "settings.write": "Update application settings",
}


@dataclass(frozen=True, slots=True)
class RoleSpec:
    name: str
    description: str
    permissions: tuple[str, ...] = field(default_factory=tuple)


_ROLE_PERMISSION_MATRIX: dict[str, tuple[str, ...]] = {
    "super_admin": tuple(BUILT_IN_PERMISSION_DEFINITIONS.keys()),
    "admin": (
        "users.read",
        "users.write",
        "roles.read",
        "roles.write",
        "stocks.read",
        "stocks.write",
        "market_data.write",
        "scanners.read",
        "scanners.run",
        "analytics.read",
        "ai.read",
        "dashboard.read",
        "portfolio.read",
        "portfolio.write",
        "watchlists.read",
        "watchlists.write",
        "alerts.read",
        "alerts.write",
        "notifications.read",
        "notifications.write",
        "pipeline.run",
        "daily_summary.run",
        "scheduler.run",
        "settings.read",
        "settings.write",
    ),
    "analyst": (
        "stocks.read",
        "market_data.write",
        "scanners.read",
        "scanners.run",
        "analytics.read",
        "ai.read",
        "dashboard.read",
        "portfolio.read",
        "portfolio.write",
        "watchlists.read",
        "watchlists.write",
        "alerts.read",
        "alerts.write",
        "notifications.read",
    ),
    "viewer": (
        "dashboard.read",
        "stocks.read",
        "scanners.read",
        "analytics.read",
        "ai.read",
        "portfolio.read",
        "watchlists.read",
        "alerts.read",
        "notifications.read",
    ),
}


def built_in_role_specs() -> tuple[RoleSpec, ...]:
    specs = []
    for name, (description, _) in BUILT_IN_ROLE_DEFINITIONS.items():
        specs.append(
            RoleSpec(
                name=name,
                description=description,
                permissions=_ROLE_PERMISSION_MATRIX[name],
            )
        )
    return tuple(specs)