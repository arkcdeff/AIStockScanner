class AuthenticationError(Exception):
    """Base exception for authentication-core failures."""


class PasswordPolicyError(AuthenticationError):
    def __init__(self, violations: list[str] | tuple[str, ...]) -> None:
        self.violations = tuple(violations)
        super().__init__("; ".join(self.violations))


class PasswordHashError(AuthenticationError):
    """Raised when a password cannot be hashed safely."""


class TokenError(AuthenticationError):
    """Base exception for token failures."""


class TokenConfigurationError(TokenError):
    """Raised when JWT settings cannot safely issue or validate tokens."""


class TokenExpiredError(TokenError):
    """Raised when a JWT has expired."""


class TokenSignatureError(TokenError):
    """Raised when a JWT signature cannot be verified."""


class TokenClaimsError(TokenError):
    """Raised when required JWT claims are missing or invalid."""


class RefreshTokenError(AuthenticationError):
    """Base exception for persisted refresh-token failures."""


class RefreshTokenExpiredError(RefreshTokenError):
    """Raised when a refresh token or its bound session has expired."""


class RefreshTokenRevokedError(RefreshTokenError):
    """Raised when a refresh token belongs to a revoked session."""


class RefreshTokenReplayError(RefreshTokenError):
    """Raised when a rotated or otherwise replaced refresh token is reused."""


class SessionBindingError(AuthenticationError):
    """Raised when token claims do not match a persisted user session."""


class InactiveUserError(AuthenticationError):
    """Raised when an inactive user is resolved from an otherwise valid token."""


class AuthorizationError(AuthenticationError):
    """Raised when a principal lacks a required role or permission."""
