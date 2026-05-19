# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Agent Orchestra, please do **not** open a public issue.

Report it privately via:
- Email: `security@orchestra.dev` (placeholder — update with real contact)
- Or DM a maintainer on any community channel

We aim to acknowledge reports within 48 hours and release a fix within 7 days for critical issues.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.x (main) | ✅ Active development |

## Security Model

Agent Orchestra orchestrates AI agents; it does not execute untrusted user code directly. Key security considerations:

- **Agent Adapters** — MCP connections should use TLS. Adapters run in the worker process; sandboxing is handled at the adapter level via `adapters/sandbox.py`.
- **Pipeline Secrets** — API keys and tokens must go through `SecretRef`, never plaintext in YAML.
- **State Codec** — `EncryptingCodec` (AES-256-GCM) encrypts sensitive pipeline data at rest.
