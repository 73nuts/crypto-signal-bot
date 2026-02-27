# Security Policy

## Supported Versions

Only the latest release of the v3.x series receives security fixes. Older versions are not maintained.

| Version | Supported |
|---------|-----------|
| 3.x     | Yes       |
| < 3.0   | No        |

## Reporting a Vulnerability

Do not open a public GitHub issue for security vulnerabilities.

Please report vulnerabilities via **[GitHub Security Advisories](https://github.com/73nuts/crypto-signal-bot/security/advisories/new)** with the following information:

- A clear description of the vulnerability
- Steps to reproduce or a proof-of-concept
- Potential impact and affected components
- Your suggested fix, if any

You will receive an acknowledgment within **48 hours**. We aim to provide a resolution timeline within 7 days of acknowledgment, depending on severity and complexity.

## Scope

The following are considered in scope:

- Authentication and authorization flaws
- Remote code execution or command injection
- Exposure of API keys, credentials, or private trade data
- Data integrity issues in the signal pipeline

The following are out of scope:

- Bugs with no security impact
- Theoretical vulnerabilities without a realistic attack scenario
- Issues in third-party dependencies not directly introduced by this project

## Disclosure Policy

We follow a coordinated disclosure model. Please allow us reasonable time to patch before public disclosure.
