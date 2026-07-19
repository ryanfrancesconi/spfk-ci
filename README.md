# spfk-ci

Shared CI tooling for the spfk-packages garden.

- `.github/workflows/swift-package-ci.yml` — reusable `workflow_call` job (`swift build` + `swift test` on a fresh checkout). Each package repo calls it from its own `ci.yml`/`release.yml`.
- `scripts/check_all_ci.py` — dispatches that CI against every package with an unpushed local tag, in dependency order, before "push tags". See `CLAUDE-reference.md` in the ShadowTag repo for full usage.

Public by design: most package repos in the garden are public, and a private repo's reusable workflows can only be shared with other private repos.
