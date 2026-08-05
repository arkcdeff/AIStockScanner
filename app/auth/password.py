from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from argon2 import PasswordHasher, Type
from argon2.exceptions import HashingError, InvalidHashError, VerificationError

from app.auth.exceptions import PasswordHashError, PasswordPolicyError
from app.core.config import settings


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    min_length: int = 12
    require_upper: bool = True
    require_lower: bool = True
    require_digit: bool = True
    require_special: bool = True


DEFAULT_PASSWORD_POLICY = PasswordPolicy()


@lru_cache
def get_password_hasher() -> PasswordHasher:
    if settings.password_hash_scheme != "argon2id":
        raise PasswordHashError("Unsupported password hash scheme")
    return PasswordHasher(
        time_cost=3,
        memory_cost=65536,
        parallelism=4,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )


@lru_cache
def _dummy_password_hash() -> str:
    return get_password_hasher().hash("Invalid-password-for-timing-only-1!")


def password_policy_violations(
    password: str, policy: PasswordPolicy = DEFAULT_PASSWORD_POLICY
) -> tuple[str, ...]:
    violations: list[str] = []
    if len(password) < policy.min_length:
        violations.append(f"Password must be at least {policy.min_length} characters")
    if policy.require_upper and not any(character.isupper() for character in password):
        violations.append("Password must contain an uppercase letter")
    if policy.require_lower and not any(character.islower() for character in password):
        violations.append("Password must contain a lowercase letter")
    if policy.require_digit and not any(character.isdigit() for character in password):
        violations.append("Password must contain a digit")
    if policy.require_special and not any(
        not character.isalnum() and not character.isspace() for character in password
    ):
        violations.append("Password must contain a special character")
    return tuple(violations)


def validate_password(password: str, policy: PasswordPolicy = DEFAULT_PASSWORD_POLICY) -> None:
    violations = password_policy_violations(password, policy)
    if violations:
        raise PasswordPolicyError(violations)


def hash_password(
    password: str,
    *,
    policy: PasswordPolicy = DEFAULT_PASSWORD_POLICY,
    validate_policy: bool = True,
) -> str:
    if validate_policy:
        validate_password(password, policy)
    try:
        return get_password_hasher().hash(password)
    except HashingError as exc:
        raise PasswordHashError("Password hashing failed") from exc


def verify_password(password: str, password_hash: str) -> bool:
    hasher = get_password_hasher()
    is_argon2_hash = password_hash.startswith("$argon2")
    candidate_hash = password_hash if is_argon2_hash else _dummy_password_hash()
    try:
        verified = hasher.verify(candidate_hash, password)
    except (InvalidHashError, VerificationError):
        return False
    return bool(verified and is_argon2_hash)


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return get_password_hasher().check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def verify_and_update_password(password: str, password_hash: str) -> tuple[bool, str | None]:
    if not verify_password(password, password_hash):
        return False, None
    if password_needs_rehash(password_hash):
        return True, hash_password(password, validate_policy=False)
    return True, None
