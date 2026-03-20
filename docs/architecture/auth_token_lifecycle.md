# W01-S02: Auth Token Lifecycle

## Goal
Define a secure and operationally practical token lifecycle for admin/trader access.

## Token model
- Access token: JWT, short-lived (15 minutes).
- Refresh token: opaque, server-stored, rotated on each refresh.
- Session record: persisted in DB/Redis for revocation and audit.

## Claims (access token)
- `sub`: user id
- `role`: admin/trader
- `permissions_version`: integer for forced permission refresh
- `jti`: token id
- `iat`, `exp`

## Flows
1. Login
   - Validate credentials.
   - Issue access + refresh tokens.
   - Store hashed refresh token with device/session metadata.
2. Refresh
   - Validate refresh token exists and not revoked.
   - Rotate refresh token (old one invalidated).
   - Issue new access token.
3. Logout
   - Revoke session refresh token(s) for current device or all devices.
4. Forced logout/security event
   - Admin/security action revokes all refresh tokens for user.

## Expiration policy
- Access token TTL: 15m
- Refresh token TTL: 7d (configurable to 30d for trusted devices)
- Idle timeout: 24h inactivity revokes session

## Security controls
- Store refresh tokens hashed (never plain text in DB).
- Enable token replay protection via `jti` and rotation.
- Enforce IP/user-agent anomaly checks for refresh endpoint.
- Rate limit auth endpoints and add progressive lockout policy.

## Operational notes
- Keep small clock-skew tolerance (+/- 60 seconds).
- Add audit events for login, refresh, logout, revocation.
- Include `correlation_id` for auth events.

## Open implementation questions
- Should privileged admin sessions require MFA in v1?
- Do we allow concurrent sessions per user; if yes, what limit?
- Is cross-device logout required from UI in first release?
