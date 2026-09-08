# Copyright (c) 2026 MiniStack Contributors. SPDX-License-Identifier: MIT
# Copies or substantial portions, including AI-assisted ports or rewrites, must retain this notice (see LICENSE).

import asyncio
import time

import pytest

from ministack.core.aws_credentials import (
    AmbiguousAccessKeyError,
    CredentialResolutionError,
    ResolvedCredential,
    find_iam_access_key_account,
    resolve_credential,
)
from ministack.core.iam_evaluator import PrincipalInfo, resolve_principal
from ministack.services import iam as iam_svc
from ministack.services import sts as sts_svc


def test_resolve_root_credential_from_environment(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "configured-root")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "configured-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "configured-token")

    credential = resolve_credential(
        "configured-root", "123456789012", "configured-token"
    )

    assert isinstance(credential, ResolvedCredential)
    assert credential.secret_access_key == "configured-secret"
    assert credential.session_token == "configured-token"
    assert credential.principal_arn == "arn:aws:iam::123456789012:root"


def test_resolve_root_credential_treats_empty_environment_token_as_absent(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "configured-root")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "")

    credential = resolve_credential("configured-root", "123456789012", "")

    assert isinstance(credential, ResolvedCredential)
    assert credential.session_token is None


def test_resolve_numeric_root_accepts_optional_ambient_session_token(monkeypatch):
    account_id = "123456789012"
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "configured-root")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "configured-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "ambient-token")

    with_token = resolve_credential(account_id, account_id, "ambient-token")
    without_token = resolve_credential(account_id, account_id, "")
    wrong_token = resolve_credential(account_id, account_id, "wrong-token")

    assert isinstance(with_token, ResolvedCredential)
    assert with_token.session_token == "ambient-token"
    assert isinstance(without_token, ResolvedCredential)
    assert without_token.session_token is None
    assert isinstance(wrong_token, CredentialResolutionError)
    assert wrong_token.code == "InvalidToken"


def test_resolve_iam_credential_is_account_scoped_and_requires_active_status():
    access_key = "AKIATESTSCOPED00001"
    first_account = "111111111111"
    second_account = "222222222222"
    iam_svc._access_keys.set_scoped(first_account, None, access_key, {
        "AccessKeyId": access_key,
        "SecretAccessKey": "first-secret",
        "Status": "Active",
        "UserName": "first-user",
    })
    iam_svc._access_keys.set_scoped(second_account, None, access_key, {
        "AccessKeyId": access_key,
        "SecretAccessKey": "second-secret",
        "Status": "Inactive",
        "UserName": "second-user",
    })
    try:
        first = resolve_credential(access_key, first_account, "")
        second = resolve_credential(access_key, second_account, "")

        assert isinstance(first, ResolvedCredential)
        assert first.secret_access_key == "first-secret"
        assert first.principal_arn == (
            "arn:aws:iam::111111111111:user/first-user"
        )
        assert isinstance(second, CredentialResolutionError)
        assert second.code == "InvalidClientTokenId"
        with pytest.raises(AmbiguousAccessKeyError):
            find_iam_access_key_account(access_key)
    finally:
        iam_svc._access_keys.pop_scoped(first_account, None, access_key, None)
        iam_svc._access_keys.pop_scoped(second_account, None, access_key, None)


def test_resolve_sts_credential_checks_token_expiry_and_origin():
    access_key = "ASIATESTSESSION0001"
    account_id = "123456789012"
    sts_svc._sessions[access_key] = {
        "Arn": f"arn:aws:iam::{account_id}:user/alice",
        "UserId": "AIDAALICE",
        "SecretAccessKey": "session-secret",
        "SessionToken": "session-token",
        "Expiration": time.time() + 60,
        "AccountId": account_id,
        "PrincipalType": "User",
        "SourceAccessKeyId": "AKIAALICE",
    }
    try:
        credential = resolve_credential(access_key, account_id, "session-token")
        wrong = resolve_credential(access_key, account_id, "wrong-token")
        missing = resolve_credential(access_key, account_id, "")
        non_ascii = resolve_credential(access_key, account_id, "not-valid-☃")

        assert isinstance(credential, ResolvedCredential)
        assert credential.principal_type == "User"
        assert credential.principal_name == "alice"
        assert credential.source_access_key_id == "AKIAALICE"
        assert isinstance(wrong, CredentialResolutionError)
        assert wrong.code == "InvalidToken"
        assert isinstance(missing, CredentialResolutionError)
        assert missing.code == "InvalidToken"
        assert isinstance(non_ascii, CredentialResolutionError)
        assert non_ascii.code == "InvalidToken"

        sts_svc._sessions[access_key]["Expiration"] = time.time() - 1
        expired = resolve_credential(access_key, account_id, "session-token")
        assert isinstance(expired, CredentialResolutionError)
        assert expired.code == "ExpiredTokenException"
    finally:
        sts_svc._sessions.pop(access_key, None)


def test_find_iam_access_key_account_returns_unique_owner():
    access_key = "test-account-lookup-key"
    account_id = "123456789012"
    iam_svc._access_keys.set_scoped(account_id, None, access_key, {
        "AccessKeyId": access_key,
        "SecretAccessKey": "secret",
        "Status": "Active",
        "UserName": "alice",
    })
    try:
        assert find_iam_access_key_account(access_key) == account_id
    finally:
        iam_svc._access_keys.pop_scoped(account_id, None, access_key, None)


def test_websocket_iam_access_key_routes_to_owner(monkeypatch):
    from ministack import app as app_mod
    from ministack.core.responses import get_account_id, set_request_account_id

    access_key = "test-websocket-owner-key"
    account_id = "123456789012"
    original_account = get_account_id()
    routed_accounts = []
    sent = []

    class AppSyncModule:
        async def handle_websocket(self, scope, receive, send, api_id):
            routed_accounts.append(get_account_id())

    monkeypatch.setattr(app_mod, "_get_module", lambda _name: AppSyncModule())
    iam_svc._access_keys.set_scoped(account_id, None, access_key, {
        "AccessKeyId": access_key,
        "SecretAccessKey": "secret",
        "Status": "Active",
        "UserName": "alice",
    })
    scope = {
        "type": "websocket",
        "path": "/event/realtime",
        "headers": [(b"host", b"api.appsync-realtime-api.localhost")],
        "query_string": (
            f"X-Amz-Credential={access_key}/20260908/us-east-1/appsync/aws4_request"
        ).encode(),
    }

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    try:
        asyncio.run(app_mod.app(scope, receive, send))

        assert routed_accounts == [account_id]
        assert sent == []
        assert app_mod._ws_resolve_iot_account_id(scope, {}) == account_id
    finally:
        iam_svc._access_keys.pop_scoped(account_id, None, access_key, None)
        set_request_account_id(original_account)


def test_ambiguous_iam_access_key_is_rejected_before_http_routing():
    from ministack import app as app_mod
    from ministack.core.responses import get_account_id, set_request_account_id

    access_key = "test-ambiguous-http-key"
    accounts = ("000000000000", "123456789012")
    original_account = get_account_id()
    sent = []
    for account_id in accounts:
        iam_svc._access_keys.set_scoped(account_id, None, access_key, {
            "AccessKeyId": access_key,
            "SecretAccessKey": f"secret-{account_id}",
            "Status": "Active",
            "UserName": f"user-{account_id}",
        })
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"host", b"s3.localhost")],
        "query_string": (
            f"X-Amz-Credential={access_key}/20260908/us-east-1/s3/aws4_request"
        ).encode(),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    try:
        asyncio.run(app_mod.app(scope, receive, send))

        assert sent[0]["type"] == "http.response.start"
        assert sent[0]["status"] == 403
        assert b"InvalidClientTokenId" in sent[1]["body"]
    finally:
        for account_id in accounts:
            iam_svc._access_keys.pop_scoped(account_id, None, access_key, None)
        set_request_account_id(original_account)


def test_ambiguous_iam_access_key_closes_websocket():
    from ministack import app as app_mod
    from ministack.core.responses import get_account_id, set_request_account_id

    access_key = "test-ambiguous-websocket-key"
    accounts = ("000000000000", "123456789012")
    original_account = get_account_id()
    sent = []
    for account_id in accounts:
        iam_svc._access_keys.set_scoped(account_id, None, access_key, {
            "AccessKeyId": access_key,
            "SecretAccessKey": f"secret-{account_id}",
            "Status": "Active",
            "UserName": f"user-{account_id}",
        })
    scope = {
        "type": "websocket",
        "path": "/event/realtime",
        "headers": [(b"host", b"api.appsync-realtime-api.localhost")],
        "query_string": (
            f"X-Amz-Credential={access_key}/20260908/us-east-1/appsync/aws4_request"
        ).encode(),
    }

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    try:
        asyncio.run(app_mod.app(scope, receive, send))

        assert sent == [{"type": "websocket.close", "code": 1008}]
    finally:
        for account_id in accounts:
            iam_svc._access_keys.pop_scoped(account_id, None, access_key, None)
        set_request_account_id(original_account)


def test_resolve_get_session_token_principal_retains_user_policies():
    access_key = "test-session-access-key"
    account_id = "123456789012"
    user_name = "alice"
    iam_svc._users.set_scoped(account_id, None, user_name, {
        "UserName": user_name,
        "UserId": "AIDAALICE",
        "AttachedPolicies": [],
    })
    iam_svc._user_inline_policies[user_name] = {
        "allow-s3": {
            "Statement": [{
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": "*",
            }],
        },
    }
    sts_svc._sessions[access_key] = {
        "Arn": f"arn:aws:iam::{account_id}:user/team/{user_name}",
        "UserId": "AIDAALICE",
        "SecretAccessKey": "session-secret",
        "SessionToken": "session-token",
        "Expiration": time.time() + 60,
        "AccountId": account_id,
        "PrincipalType": "User",
        "PrincipalName": user_name,
        "SourceAccessKeyId": "AKIAALICE",
    }
    try:
        principal = resolve_principal(access_key, account_id)

        assert isinstance(principal, PrincipalInfo)
        assert principal.type == "User"
        assert principal.arn == (
            f"arn:aws:iam::{account_id}:user/team/{user_name}"
        )
        assert principal.policies
        assert principal.policies[0][0].actions == ["s3:GetObject"]
    finally:
        sts_svc._sessions.pop(access_key, None)
        iam_svc._user_inline_policies.pop(user_name, None)
        iam_svc._users.pop_scoped(account_id, None, user_name, None)
