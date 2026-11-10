# Security Policy

## Supported version

Security fixes are applied to the latest release on the `main` branch.

## Reporting a vulnerability

Do not put credentials, Pixiv cookies, local paths, downloaded artwork, or exploit details in a public issue.

Before making the repository public, enable GitHub **Private vulnerability reporting** under **Settings > Security > Code security and analysis**. Report vulnerabilities through the repository's **Security > Advisories > Report a vulnerability** form.

A useful report includes:

- affected MOKU version and Windows version;
- a minimal reproduction using test data;
- expected and observed behavior;
- security impact;
- whether the issue involves the loopback API, filesystem writes, login state, or network routing.

Remove secrets and personal data from logs. MOKU HTTP logs intentionally omit query parameters and request bodies.

## Release authenticity

Signed release builds verify their bundled manifest and managed program files locally at startup. The updater requires Ed25519-signed release metadata and file manifests; SHA-256 alone is not treated as proof of authorship. Missing or invalid signatures stop the update without replacing the working installation. User settings, credentials, caches and downloads are outside the signed component set.

See [RELEASE_SIGNING.md](RELEASE_SIGNING.md) for bootstrap trust, private-key protection, encrypted backups, release steps, key rotation and limitations. Windows Authenticode publisher signing is separate and requires a certificate; an Ed25519 signature does not imply Windows publisher trust. This is integrity and provenance protection, not DRM or a restriction on MIT-licensed modifications.

## Local service boundaries

MOKU is designed for one Windows user on one machine:

- the service binds only to `127.0.0.1`;
- API requests require a loopback host and same-origin browser context;
- mutating API calls also require JSON and a per-process request token;
- Pixiv API and image requests use explicit HTTPS host allowlists;
- only loopback HTTP proxies are accepted;
- MOKU does not modify Windows proxy settings, launch VPN software, or scan ports;
- LAN and Internet exposure are unsupported.

Changing the bind address or filesystem scope requires a new threat model, authentication, and transport security.
