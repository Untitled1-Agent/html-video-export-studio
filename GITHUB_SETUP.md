# Create the repository and publish releases

[Back to README](README.md)

## Account connection versus repository write access

A connected read/search integration does not automatically provide a terminal with GitHub CLI credentials or repository-creation operations. The helper here uses **your local GitHub CLI login**, not an exported connector token. No remote repository is created by extracting the ZIP or running `--dry-run`.

The intended default name is `html-video-export-studio`; initial visibility is **private**. The owner/name can be supplied explicitly. This package has no selected project license; choose one and review the source before making it public.

## Install GitHub CLI and Git

Install Git and GitHub CLI from their official distribution instructions. Typical Windows commands are:

```powershell
winget install --id Git.Git -e
winget install --id GitHub.cli -e
```

Open a fresh terminal and verify `git --version` and `gh --version`. macOS can use `brew install git gh`; Linux users should follow GitHub CLI's distribution-specific installation instructions. Do not download random replacement installers. Official links are in [references](docs/REFERENCES.md).

Authenticate on your own machine:

```bash
gh auth login --hostname github.com --web --git-protocol https --scopes workflow
gh auth setup-git --hostname github.com
gh auth status --hostname github.com
```

The additional workflow scope is for pushing the included workflow files through an OAuth/classic-token login; organization and fine-grained permission rules can still apply. Never paste credentials into chat. GitHub CLI manages local credentials; inspect its official guidance and your OS credential store.

## Publish the prepared source

Extract the **GitHub-ready** ZIP into a fresh folder. From inside `HTML-Video-Export-Studio`:

```bash
python tools/publish_github.py --repo html-video-export-studio --dry-run
python tools/publish_github.py --repo html-video-export-studio
```

To target the account explicitly, use `--repo OWNER/html-video-export-studio`, substituting the actual owner. A dry run lists exactly the files to stage and the create command, without touching Git or the network. An actual run checks authentication/name availability, initializes `main` when needed, stages only the package allowlist, commits, and calls `gh repo create ... --private --source ... --remote origin --push`.

For a new checkout with no Git identity, only missing **local** name/email are set from the authenticated login and its GitHub no-reply address. Existing Git identity settings are respected. Existing repository names, existing remotes, non-main checkouts, and unexpected tracked/staged files are rejected. The helper never force-pushes or deletes a repository. A failure after remote creation may leave the new repo and origin in place; inspect it and repair authentication/push manually rather than recreating/deleting it blindly.

The helper stages the current allowlisted tree, not arbitrary local customer inputs. Existing Git history can contain previously committed secrets even after they were deleted; use a fresh archive for initial publication or audit history before pushing. To deliberately create a public repo add `--public`; it is never the default.

## Repository settings after creation

Enable Issues for bug reports; issue forms and a PR template are included. Set a default branch protection/ruleset requiring the Tests workflow once its checks have actually run. Review who can write code or publish releases. Enable private vulnerability reporting/secret scanning where your account offers them. Dependabot configuration proposes Python/GitHub Actions updates; those PRs must pass tests, not be blindly merged.

No branch protection, hosted issue closure, Actions success badge, or remote release is claimed until the relevant operations have completed on GitHub.

## First release

Wait for Tests on `main` to pass, then create a version tag matching `version.py`:

```bash
git tag -a v1.5.2 -m "HTML Video Export Studio 1.5.2"
git push origin v1.5.2
```

The Release workflow reruns Linux validation/profile/docs checks, packages source/update ZIPs and checksums, and creates a **draft** release. Review its notes/assets, then publish the draft deliberately. The app's latest-stable updater ignores drafts/prereleases.

For manual release creation, run `python tools/package_release.py dist`, then:

```bash
gh release create v1.5.2 dist/*.zip dist/*.sha256 --verify-tag --draft --title "HTML Video Export Studio 1.5.2" --notes-file RELEASE_NOTES_1.5.2.md
```

Use the owner/repo in the app Settings & Updates. A `.git` origin may be detected automatically; ZIP users enter the repo manually. For private release downloads set `HTML_MP4_GITHUB_TOKEN` locally with only the needed access. [Release operations](docs/RELEASING.md) explains naming, updates and recovery.
