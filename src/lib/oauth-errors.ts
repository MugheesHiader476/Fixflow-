/** Fixed public messages only: provider text, descriptions and credentials are never reflected. */
export const OAUTH_SIGN_IN_RETURN = "/connectors?connection_error=sign_in";

export const CONNECTION_ERRORS = {
  authorization: "Authorization did not complete. Check the provider app, registered callback URL and required permissions, then reconnect.",
  api_disabled: "The Google API is disabled in your OAuth app's Cloud project. Enable Google Drive API or Gmail API in Google Cloud, then reconnect.",
  missing_scope: "Required provider permissions were not granted. Reconnect and approve the listed read permissions.",
  reauth_required: "Provider authorization expired, was revoked, or could not be refreshed. Reconnect this account.",
  invalid_state: "This authorization link expired or was already used. Start a new connection in the signed-in account.",
  sign_in: "Your sign-in expired during authorization. Sign in to FixFlow and start a new connection.",
  provider_unavailable: "The provider is temporarily unavailable. Try connecting again later.",
  rate_limited: "The provider is rate limited. Try connecting again later.",
  inaccessible: "The provider denied data access. Check API enablement, app permissions and your organization's access policy, then reconnect.",
  not_configured: "The server's connector setup is incomplete or its gateway credentials were rejected. Check the server configuration.",
  provider_error: "The provider rejected the request. Check the provider app configuration and permissions.",
  oauth_client_invalid: "The provider rejected the app's OAuth client ID or secret. Check the server credentials in the provider app settings.",
  oauth_code_invalid: "The authorization code could not be validated. It may be expired, already used or mismatched; start a new connection.",
  oauth_redirect_mismatch: "The OAuth redirect URL was rejected. Register the exact callback URL shown in connection setup and use the same URL throughout authorization.",
  oauth_access_denied: "Provider access was declined or blocked by an account or organization policy. Check approval and policy settings before reconnecting.",
  oauth_scope_invalid: "The requested OAuth scopes are invalid or not approved for this provider app. Check its permission configuration.",
  oauth_request_invalid: "The provider rejected the OAuth request parameters. Check the app's OAuth configuration.",
  oauth_authorization_failed: "The provider rejected authorization with an unrecognized error. Check the provider app configuration and start a new connection.",
  oauth_exchange_uncertain: "The authorization response was lost or invalid. The code was not retried; start a new connection.",
  oauth_refresh_uncertain: "The token refresh response was lost or invalid. Automatic refresh has stopped; reconnect this account.",
} as const;

export type OAuthFailureCode = keyof typeof CONNECTION_ERRORS;

export function knownOAuthFailure(value: string): OAuthFailureCode | null {
  const key = value.toLowerCase();
  return Object.hasOwn(CONNECTION_ERRORS, key) ? key as OAuthFailureCode : null;
}

export function providerAuthorizationFailure(value: string | null): OAuthFailureCode {
  switch (value) {
    case "access_denied": case "user_denied": case "admin_policy_enforced": case "org_internal":
    case "invalid_team_for_non_distributed_app": case "enterprise_is_restricted": return "oauth_access_denied";
    case "invalid_client": case "invalid_client_id": case "unauthorized_client": return "oauth_client_invalid";
    case "redirect_uri_mismatch": case "bad_redirect_uri": case "invalid_redirect_uri": return "oauth_redirect_mismatch";
    case "invalid_scope": case "unapproved_scope": case "scope_not_allowed_on_enterprise": return "oauth_scope_invalid";
    case "invalid_request": case "unsupported_response_type": return "oauth_request_invalid";
    case "temporarily_unavailable": case "server_error": return "provider_unavailable";
    default: return "authorization";
  }
}
