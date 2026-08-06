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
    "market_data.write": "Import and refresh market data",
    "scanners.run": "Execute equity scanners",
    "portfolio.read": "View portfolios and holdings",
    "portfolio.write": "Create and update portfolios",
    "alerts.read": "View price alerts",
    "alerts.write": "Create and manage price alerts",
    "notifications.read": "View notification history and preferences",
    "notifications.write": "Manage notification channels and preferences",
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
        "market_data.write",
        "scanners.run",
        "portfolio.read",
        "portfolio.write",
        "alerts.read",
        "alerts.write",
        "notifications.read",
        "notifications.write",
        "settings.write",
    ),
    "analyst": (
        "stocks.read",
        "market_data.write",
        "scanners.run",
        "portfolio.read",
        "alerts.read",
        "notifications.read",
    ),
    "viewer": (
        "stocks.read",
        "portfolio.read",
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