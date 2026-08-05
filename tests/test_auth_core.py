from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from argon2 import PasswordHasher, Type

from app.auth.exceptions import (
    AuthorizationError,
    RefreshTokenExpiredError,
    RefreshTokenReplayError,
    RefreshTokenRevokedError,
    SessionBindingError,
    TokenClaimsError,
    TokenConfigurationError,
    TokenExpiredError,
    TokenSignatureError,
)
from app.auth.jwt import (
    JWTKeyRing,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)
from app.auth.password import (
    PasswordPolicyError,
    hash_password,
    password_needs_rehash,
    password_policy_violations,
    validate_password,
    verify_and_update_password,
    verify_password,
)
from app.auth.refresh_tokens import (
    create_refresh_session,
    hash_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
    validate_refresh_token,
)
from app.auth.security import (
    extract_bearer_token,
    extract_principal,
    has_permission,
    has_role,
    require_permissions,
    require_roles,
    resolve_current_user,
)
from app.db.session import get_session_factory
from app.models.auth import User

CURRENT_SECRET = "c" * 64
OLD_SECRET = "o" * 64
NEW_SECRET = "n" * 64
CURRENT_RING = JWTKeyRing.from_config(f"current:{CURRENT_SECRET}")


def _user(*, is_active: bool = True) -> User:
    return User(
        username="auth-user",
        email="auth-user@example.com",
        full_name="Auth User",
        password_hash="stored-hash-only",
        is_active=is_active,
    )


def test_password_hashing_and_verification_use_argon2id() -> None:
    password = "Correct-Horse-7!"

    password_hash = hash_password(password)

    assert password_hash.startswith("$argon2id$")
    assert password not in password_hash
    assert verify_password(password, password_hash) is True
    assert verify_password("Wrong-Horse-7!", password_hash) is False
    assert verify_password(password, "not-a-valid-hash") is False
    assert password_needs_rehash(password_hash) is False


def test_password_policy_reports_each_required_character_class() -> None:
    cases = {
        "Short1!": "at least 12",
        "lowercase-only-1!": "uppercase",
        "UPPERCASE-ONLY-1!": "lowercase",
        "No-Digit-Here!": "digit",
        "NoSpecialCharacter7": "special",
    }

    for password, expected_message in cases.items():
        violations = password_policy_violations(password)
        assert any(expected_message in violation for violation in violations)
        with pytest.raises(PasswordPolicyError, match=expected_message):
            validate_password(password)

    validate_password("Policy-Passes-7!")


def test_password_hash_is_upgraded_after_successful_verification() -> None:
    password = "Upgrade-Password-8!"
    legacy_hasher = PasswordHasher(
        time_cost=1,
        memory_cost=8192,
        parallelism=1,
        hash_len=16,
        salt_len=16,
        type=Type.ID,
    )
    legacy_hash = legacy_hasher.hash(password)

    verified, upgraded_hash = verify_and_update_password(password, legacy_hash)

    assert verified is True
    assert upgraded_hash is not None
    assert upgraded_hash != legacy_hash
    assert verify_password(password, upgraded_hash) is True
    assert password_needs_rehash(upgraded_hash) is False


def test_jwt_access_and_refresh_tokens_contain_required_claims() -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    access_token = create_access_token(
        subject=42,
        session_id=9,
        roles=["analyst", "analyst"],
        permissions=["stocks.read", "alerts.write"],
        now=now,
        key_ring=CURRENT_RING,
    )
    refresh_token = create_refresh_token(
        subject=42,
        session_id=9,
        roles=["analyst"],
        permissions=["stocks.read"],
        now=now,
        key_ring=CURRENT_RING,
    )

    access = decode_access_token(access_token, key_ring=CURRENT_RING)
    refresh = decode_refresh_token(refresh_token, key_ring=CURRENT_RING)

    assert access.subject == "42"
    assert access.session_id == 9
    assert access.roles == ("analyst",)
    assert access.permissions == ("stocks.read", "alerts.write")
    assert access.issued_at == now
    assert access.not_before == now
    assert access.expires_at > now
    assert access.issuer == "ai-stock-scanner"
    assert access.audience == "ai-stock-scanner"
    assert access.token_type == "access"
    assert refresh.token_type == "refresh"
    assert refresh.token_id
    assert (
        refresh.token_id
        != decode_refresh_token(
            create_refresh_token(
                subject=42,
                session_id=9,
                now=now,
                key_ring=CURRENT_RING,
            ),
            key_ring=CURRENT_RING,
        ).token_id
    )


def test_jwt_rejects_expired_wrongly_signed_and_wrongly_scoped_tokens() -> None:
    expired = create_access_token(
        subject=1,
        session_id=1,
        now=datetime.now(UTC) - timedelta(hours=1),
        expires_delta=timedelta(minutes=1),
        key_ring=CURRENT_RING,
    )
    valid = create_access_token(subject=1, session_id=1, key_ring=CURRENT_RING)
    other_ring = JWTKeyRing.from_config(f"current:{'x' * 64}")

    with pytest.raises(TokenExpiredError):
        decode_access_token(expired, key_ring=CURRENT_RING, clock_skew_seconds=0)
    with pytest.raises(TokenSignatureError):
        decode_access_token(valid, key_ring=other_ring)
    with pytest.raises(TokenClaimsError):
        decode_access_token(valid, key_ring=CURRENT_RING, audience="wrong-audience")
    with pytest.raises(TokenClaimsError):
        decode_access_token(valid, key_ring=CURRENT_RING, issuer="wrong-issuer")
    with pytest.raises(TokenClaimsError):
        decode_refresh_token(valid, key_ring=CURRENT_RING)


def test_jwt_key_rotation_and_clock_skew() -> None:
    old_ring = JWTKeyRing.from_config(f"old:{OLD_SECRET}")
    rotated_ring = JWTKeyRing.from_config(f"current:{NEW_SECRET},old:{OLD_SECRET}")
    old_token = create_access_token(subject=3, session_id=4, key_ring=old_ring)
    new_token = create_access_token(subject=3, session_id=4, key_ring=rotated_ring)
    near_future_token = create_access_token(
        subject=3,
        session_id=4,
        now=datetime.now(UTC) + timedelta(seconds=20),
        key_ring=rotated_ring,
    )

    assert decode_access_token(old_token, key_ring=rotated_ring).subject == "3"
    assert pyjwt.get_unverified_header(new_token)["kid"] == "current"
    assert (
        decode_access_token(
            near_future_token,
            key_ring=rotated_ring,
            clock_skew_seconds=30,
        ).session_id
        == 4
    )
    with pytest.raises(TokenClaimsError):
        decode_access_token(
            near_future_token,
            key_ring=rotated_ring,
            clock_skew_seconds=0,
        )


def test_jwt_configuration_requires_a_strong_unique_key_ring() -> None:
    with pytest.raises(TokenConfigurationError):
        JWTKeyRing.from_config("")
    with pytest.raises(TokenConfigurationError):
        JWTKeyRing.from_config("too-short")
    with pytest.raises(TokenConfigurationError):
        JWTKeyRing.from_config(f"same:{CURRENT_SECRET},same:{OLD_SECRET}")


@pytest.mark.asyncio
async def test_refresh_token_is_hashed_rotated_and_old_value_is_rejected() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = _user()
        session.add(user)
        await session.flush()
        issued = await create_refresh_session(
            session,
            user_id=user.id,
            roles=["analyst"],
            permissions=["stocks.read"],
            user_agent="pytest",
            ip_address="127.0.0.1",
            key_ring=CURRENT_RING,
        )

        assert issued.token_hash == hash_refresh_token(issued.token)
        assert issued.session.refresh_token_hash != issued.token
        assert issued.token not in issued.session.refresh_token_hash
        record, claims = await validate_refresh_token(session, issued.token, key_ring=CURRENT_RING)
        assert record.id == claims.session_id
        assert claims.subject == str(user.id)

        rotated = await rotate_refresh_token(
            session,
            issued.token,
            permissions=["stocks.read", "alerts.write"],
            key_ring=CURRENT_RING,
        )
        assert rotated.token != issued.token
        assert rotated.token_hash == rotated.session.refresh_token_hash
        rotated_claims = decode_refresh_token(rotated.token, key_ring=CURRENT_RING)
        assert rotated_claims.permissions == ("stocks.read", "alerts.write")
        with pytest.raises(RefreshTokenReplayError):
            await validate_refresh_token(session, issued.token, key_ring=CURRENT_RING)
        await session.rollback()


@pytest.mark.asyncio
async def test_refresh_token_revocation_and_persisted_expiry_are_enforced() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = _user()
        session.add(user)
        await session.flush()
        issued = await create_refresh_session(session, user_id=user.id, key_ring=CURRENT_RING)
        revoked = await revoke_refresh_token(session, issued.token, key_ring=CURRENT_RING)
        assert revoked.revoked_at is not None
        with pytest.raises(RefreshTokenRevokedError):
            await validate_refresh_token(session, issued.token, key_ring=CURRENT_RING)

        second = await create_refresh_session(session, user_id=user.id, key_ring=CURRENT_RING)
        second.session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.flush()
        with pytest.raises(RefreshTokenExpiredError):
            await validate_refresh_token(session, second.token, key_ring=CURRENT_RING)
        await session.rollback()


@pytest.mark.asyncio
async def test_refresh_token_session_binding_rejects_a_different_user() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = _user()
        session.add(user)
        await session.flush()
        issued = await create_refresh_session(session, user_id=user.id, key_ring=CURRENT_RING)
        mismatched = create_refresh_token(
            subject=user.id + 1,
            session_id=issued.session.id,
            key_ring=CURRENT_RING,
        )
        issued.session.refresh_token_hash = hash_refresh_token(mismatched)
        await session.flush()

        with pytest.raises(SessionBindingError):
            await validate_refresh_token(session, mismatched, key_ring=CURRENT_RING)
        await session.rollback()


@pytest.mark.asyncio
async def test_security_helpers_extract_access_and_enforce_authorization() -> None:
    factory = get_session_factory()
    async with factory() as session:
        user = _user()
        session.add(user)
        await session.flush()
        refresh = await create_refresh_session(session, user_id=user.id, key_ring=CURRENT_RING)
        access_token = create_access_token(
            subject=user.id,
            session_id=refresh.session.id,
            roles=["analyst"],
            permissions=["stocks.read", "alerts.write"],
            key_ring=CURRENT_RING,
        )

        assert extract_bearer_token(f"Bearer {access_token}") == access_token
        assert extract_bearer_token(f"bearer {access_token}") == access_token
        with pytest.raises(TokenClaimsError):
            extract_bearer_token(access_token)

        principal = extract_principal(access_token, key_ring=CURRENT_RING)
        assert principal.user_id == user.id
        assert has_role(principal, "analyst") is True
        assert has_permission(principal, "stocks.read") is True
        assert require_roles(principal, ["analyst"]) is principal
        assert require_permissions(principal, ["stocks.read", "alerts.write"]) is principal
        assert (
            require_permissions(principal, ["stocks.delete", "stocks.read"], require_all=False)
            is principal
        )
        with pytest.raises(AuthorizationError):
            require_roles(principal, ["admin"])
        with pytest.raises(AuthorizationError):
            require_permissions(principal, ["users.write"])

        current = await resolve_current_user(session, access_token, key_ring=CURRENT_RING)
        assert current.user is user
        assert current.principal is principal or current.principal == principal

        refresh.session.revoked_at = datetime.now(UTC)
        await session.flush()
        with pytest.raises(SessionBindingError):
            await resolve_current_user(session, access_token, key_ring=CURRENT_RING)
        await session.rollback()
