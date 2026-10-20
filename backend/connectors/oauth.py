"""Allowlisted OAuth diagnostics; raw provider messages and credentials never escape."""

from typing import Literal

from backend.connectors.core import ConnectorError

OAuthOperation = Literal["exchange", "refresh"]
TOKEN_ENDPOINTS = frozenset({
    "https://oauth2.googleapis.com/token",
    "https://github.com/login/oauth/access_token",
    "https://slack.com/api/oauth.v2.access",
})


def uncertain_token(operation: OAuthOperation) -> ConnectorError:
    if operation == "refresh":
        return ConnectorError(
            "oauth_refresh_uncertain",
            "The token refresh response was lost or invalid. Automatic refresh has stopped; reconnect this account.",
        )
    return ConnectorError(
        "oauth_exchange_uncertain",
        "The authorization response was lost or invalid. The code was not retried; start a new connection.",
    )


def oauth_failure(value: object, operation: OAuthOperation) -> ConnectorError:
    error = value if isinstance(value, str) else ""
    if error in {"invalid_client", "invalid_client_id", "invalid_client_secret", "bad_client_secret",
                 "incorrect_client_credentials", "unauthorized_client"}:
        return ConnectorError("oauth_client_invalid", "The provider rejected the app's OAuth client ID or secret")
    if error in {"bad_redirect_uri", "redirect_uri_mismatch", "invalid_redirect_uri", "redirect_uri_missing"}:
        return ConnectorError("oauth_redirect_mismatch", "The OAuth callback URL did not match the provider app setup")
    if error in {"invalid_code", "bad_verification_code", "code_already_used", "invalid_code_verifier"} or (
        error == "invalid_grant" and operation == "exchange"
    ):
        return ConnectorError(
            "oauth_code_invalid", "The authorization code is invalid, expired, already used or mismatched; start again"
        )
    if error in {"invalid_grant", "invalid_token", "invalid_refresh_token", "bad_refresh_token", "token_revoked",
                 "token_expired", "invalid_auth", "account_inactive"}:
        return ConnectorError(
            "reauth_required", "Provider authorization expired or was revoked; reconnect this account"
        )
    if error in {"invalid_scope", "unapproved_scope", "scope_not_allowed_on_enterprise", "missing_scope"}:
        return ConnectorError(
            "oauth_scope_invalid", "The requested OAuth scopes are invalid or not approved for this app"
        )
    if error in {"access_denied", "user_denied", "admin_policy_enforced", "org_internal", "enterprise_is_restricted",
                 "invalid_team_for_non_distributed_app"}:
        return ConnectorError("oauth_access_denied", "Provider access was declined or blocked by account policy")
    if error in {"invalid_request", "unsupported_grant_type", "invalid_grant_type", "unsupported_response_type"}:
        return ConnectorError("oauth_request_invalid", "The provider rejected the OAuth request parameters")
    if error in {"temporarily_unavailable", "server_error", "internal_error", "fatal_error"}:
        return uncertain_token(operation)
    if error in {"ratelimited", "rate_limited"}:
        return ConnectorError("rate_limited", "Provider authorization is rate limited; retry later", 60)
    return ConnectorError(
        "oauth_authorization_failed", "The provider rejected authorization with an unrecognized error"
    )
