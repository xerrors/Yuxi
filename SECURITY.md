# Security Policy

Thank you for helping keep Yuxi-Know and its users safe. We take security
seriously and appreciate the efforts of the security community to responsibly
disclose vulnerabilities.

## Supported Versions

Security fixes are applied to the **latest released version** on the `main`
branch. We recommend always running the most recent release.

| Version        | Supported          |
| -------------- | ------------------ |
| Latest release | :white_check_mark: |
| Older releases | :x:                |

If you are running an older version, please upgrade before reporting an issue,
as it may already be fixed.

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues,
pull requests, or discussions.**

Instead, use one of the private channels below:

1. **GitHub Private Vulnerability Reporting (preferred).**
   Go to the [**Security** tab](https://github.com/xerrors/Yuxi/security) of
   this repository and click **"Report a vulnerability"**. This opens a private
   advisory thread visible only to you and the maintainers.

2. **Email (fallback).**
   If private reporting is unavailable, email the maintainer at
   **ricalmars@outlook.com** with the subject line prefixed `[Security]`.

To help us triage and reproduce quickly, please include as much of the
following as you can:

- A description of the vulnerability and its potential impact.
- The affected component(s), and file paths / line numbers or commit SHA where
  relevant.
- The version, deployment configuration, and any relevant model / agent
  settings.
- Step-by-step reproduction instructions or a minimal proof-of-concept.
- Any suggested remediation, if you have one.

Please **do not** include exploit details, payloads, or live-target evidence in
public channels — keep them in the private thread.

## Coordinated Disclosure

We follow a coordinated-disclosure model:

- We ask that you give us a reasonable time to investigate and fix the issue
  before any public disclosure. Our default target is **90 days** from the
  initial report, and we are happy to agree a timeline that fits the severity.
- We will keep you informed of our progress and coordinate the disclosure date
  with you.
- With your permission, we will credit you in the published advisory and
  release notes. You may also choose to remain anonymous.

## Our Commitment (Response Targets)

When you report an issue through a private channel, we aim to:

- **Acknowledge** your report within **5 business days**.
- Provide an **initial assessment** (validity and severity) within
  **10 business days**.
- Keep you updated on remediation progress and coordinate a disclosure date.
- Publish a [GitHub Security Advisory](https://github.com/xerrors/Yuxi/security/advisories)
  (and request a CVE where appropriate) once a fix is available.

These are targets, not guarantees — Yuxi-Know is maintained by a small team, so
timelines may vary with complexity and availability.

## Scope

This policy covers the code in this repository (backend, frontend, agent
harness, and the deployment configuration shipped here, e.g. `docker-compose`
files and the sandbox provisioner).

We are particularly interested in reports involving:

- Authentication / authorization bypass and privilege escalation
  (multi-tenant / multi-user boundaries).
- Cross-tenant data access or isolation failures.
- Remote code execution, and untrusted-input paths that reach
  code-execution or file-system capabilities (including via chat, RAG /
  knowledge-base content, uploaded or OCR'd documents, and prompt injection).
- Sandbox escape or weakened container / network isolation.
- Exposure of secrets, credentials, or API keys.
- Injection issues (SQL, command, SSRF, etc.).

**Out of scope** (unless you can demonstrate a concrete security impact):

- Issues that require default/example credentials from the sample deployment
  configuration. Operators are expected to change all default secrets before
  production use; please report these as hardening suggestions rather than
  vulnerabilities.
- Findings that depend on a misconfigured, self-modified, or non-default
  deployment.
- Denial of service via unrealistic request volumes.
- Vulnerabilities in third-party dependencies that are already publicly known
  and tracked upstream (though we welcome a heads-up).
- Reports from automated scanners without a demonstrated, exploitable impact.

## Safe Harbor

We consider security research conducted in good faith and in accordance with
this policy to be authorized. We will not pursue or support legal action
against researchers who:

- Make a good-faith effort to avoid privacy violations, data destruction, and
  service disruption.
- Only interact with systems and accounts they own or have explicit permission
  to test — **please do not test against other people's live deployments or
  third-party hosted instances.**
- Report any accidental access to data that is not their own, and do not
  retain, disclose, or misuse it.
- Give us reasonable time to remediate before public disclosure.

## Past Advisories

Published security advisories for this project are listed under the
[**Security Advisories**](https://github.com/xerrors/Yuxi/security/advisories)

---

Thank you for helping make Yuxi-Know safer for everyone.
