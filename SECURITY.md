# Security policy and threat boundaries

Use the latest reviewed release and inspect its [test report](TEST_REPORT.md). This is not an independently audited hardened renderer. There is no maintained multi-version security support schedule yet.

## Trusted inputs only

HTML/URLs execute JavaScript in Chromium and may access the network. Browser contexts are separate from your normal browsing profile, but this is not a safe analysis sandbox for hostile pages. No DRM bypass, organization-policy bypass, credential extraction, or arbitrary-site correctness is supported. Render untrusted pages only in an independently secured environment managed by you; the studio does not provide that isolation.

## Files and publication

Movies and PNG previews are committed only after success/cancellation checks. External soundtracks and source HTML cannot be selected as their final outputs. Symlink/path/archive checks protect specific operations; local attacker races and whole-system hostile users are not an audited threat model. Keep output folders under your own control.

The package/publishing allowlist excludes random root HTML and common sensitive/generated paths, but code/docs/test directories are publishable. Review new files, history, projects, logs and URLs for secrets. A query token inside a URL can leak into a report even though credential-bearing user:password URLs are rejected. The GitHub helper never imports ChatGPT connector credentials, stores a token, changes an existing remote, or force-pushes. GitHub CLI authentication is separately controlled by the user's local credential setup.

## Update trust

Configure only a GitHub repository whose executable code you trust. HTTPS and an exact matching SHA-256 protect transport integrity; a malicious/compromised publisher can supply both code and checksum. Signature/attestation enforcement remains open work. Tokens for private downloads come from `HTML_MP4_GITHUB_TOKEN`; the app does not save them. Avoid personal access tokens in files or chat.

The updater bounds download/unpacked sizes, rejects traversal/case collisions/symlinks/protected paths, checks version and Python syntax, waits for app exit, backs up only affected files and rolls back handled failures. Dependency changes require manual/fresh installation. It is not power-loss atomic across the whole tree. Pip-installed copies must use pip; developers should use Git/pip for editable checkouts.

## Reporting

Use private vulnerability reporting on the repository Security tab when the owner enables it. If that feature is unavailable, obtain a private channel from the owner before sending sensitive details. No unverified maintainer email address is provided. Include affected version, minimal reproduction, impact, platform and sanitized logs; do not include actual access tokens or customer artwork.
