# Northwind Platform API Reference

## Authentication

Every request must include an API key. Keys start with the prefix nw_live_ for production and nw_test_ for the sandbox environment. Send the key in the X-NW-Key header; keys passed as query parameters are rejected.

Keys can be rotated from the developer dashboard. The old key keeps working for 24 hours after a rotation so that deployments can be updated without downtime.

## Rate Limits

Rate limits depend on your plan. The Starter plan allows 60 requests per minute, the Growth plan allows 600 requests per minute, and the Enterprise plan allows 6,000 requests per minute.

When you exceed the limit the API responds with HTTP status 429 and a Retry-After header that tells you how many seconds to wait before retrying.

## Error Codes

Error code NW-1001 means the API key is missing or invalid. Error code NW-2003 means the monthly quota for your plan has been exhausted. Error code NW-4012 means a webhook signature did not match the payload. Error code NW-5000 indicates an internal server error; these are safe to retry with exponential backoff.

## Pagination

List endpoints use cursor-based pagination. Pass the next_cursor value from the previous response to fetch the next page. The maximum page size is 200 items and the default is 50.

## Webhooks

Webhook deliveries that fail are retried 8 times over 24 hours with exponential backoff. Every delivery is signed with HMAC-SHA256 and the signature is sent in the X-NW-Signature header; verify it before trusting the payload.

## Versioning

The API is versioned by date, for example 2026-01-15. Breaking changes are only released in a new version, and deprecated versions are supported for 12 months after the deprecation notice.
