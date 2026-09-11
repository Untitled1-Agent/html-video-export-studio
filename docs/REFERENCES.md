# External implementation references

These are upstream documentation references consulted during repository preparation, separate from source-derived features and local test evidence. They do not imply upstream endorsement or that a live GitHub operation completed. Consult them for platform-specific details that may change.

- [Playwright Python library and thread ownership](https://playwright.dev/python/docs/library)
- [Playwright Clock and installation order](https://playwright.dev/python/docs/clock)
- [Python Tkinter threading model](https://docs.python.org/3/library/tkinter.html#threading-model)
- [FFmpeg filters: CAS, unsharp, scale, eq, audio filters](https://ffmpeg.org/ffmpeg-filters.html)
- [GitHub CLI installation](https://github.com/cli/cli#installation)
- [GitHub CLI browser authentication](https://cli.github.com/manual/gh_auth_login)
- [GitHub CLI Git credential setup](https://cli.github.com/manual/gh_auth_setup-git)
- [GitHub CLI repository creation](https://cli.github.com/manual/gh_repo_create)
- [GitHub Actions checkout](https://github.com/actions/checkout)
- [GitHub Actions setup-python](https://github.com/actions/setup-python)

Workflow pins were resolved during this review: checkout v7 → `3d3c42e5aac5ba805825da76410c181273ba90b1`; setup-python v7 → `5fda3b95a4ea91299a34e894583c3862153e4b97`. Pins make the selected revision explicit, not permanently secure; review updates and run CI.
