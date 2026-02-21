# Releasing

## Versioning

Potluck uses [semantic versioning](https://semver.org/). Each phase milestone maps to a minor version bump:

| Phase | Version | Branch |
|-------|---------|--------|
| Phase 1 | `0.1.x` | `phase-1-dev` |
| Phase 2 | `0.2.x` | `phase-2-dev` |
| ... | ... | ... |

Current version is defined in `pyproject.toml` under `[project] version`.

## Prerequisites

- All CI checks pass on `main` (lint, tests, browser tests)
- Push access to `main` branch
- GitHub token with `contents:write` and `packages:write` permissions (automatic for maintainers)

## Release Steps

### 1. Update the version in `pyproject.toml`

```bash
# On your phase branch, bump the version
# Edit pyproject.toml: version = "0.9.0"
```

The release workflow verifies the tag matches `pyproject.toml`, so this must be done before tagging.

### 2. Merge the phase branch to `main`

```bash
git checkout main
git pull origin main
git merge phase-9-dev
```

Resolve any conflicts. CI will run automatically on the push/PR to `main`.

### 3. Create and push the tag

```bash
git tag v0.9.0
git push origin main
git push origin v0.9.0
```

The tag **must** start with `v` (e.g., `v0.9.0`) to trigger the release workflow.

### 4. Verify the release

The [Release workflow](../.github/workflows/release.yml) runs automatically and:

1. **Builds and pushes Docker images** (parallel matrix):
   - `ghcr.io/doublegremlin181/potluck:0.9.0` (CPU)
   - `ghcr.io/doublegremlin181/potluck:latest` (CPU, updated)
   - `ghcr.io/doublegremlin181/potluck:0.9.0-gpu` (GPU)
   - `ghcr.io/doublegremlin181/potluck:gpu` (GPU, updated)
   - `ghcr.io/doublegremlin181/potluck-db:0.9.0` (Database)
   - `ghcr.io/doublegremlin181/potluck-db:latest` (Database, updated)

2. **Creates a GitHub Release** with:
   - Auto-generated changelog from commits since the previous tag
   - Prerelease flag if version contains `-` (e.g., `0.9.0-rc1`)

Monitor progress at: `https://github.com/DoubleGremlin181/potluck/actions/workflows/release.yml`

## Quick Reference

```bash
# Full release sequence
git checkout phase-9-dev
# Edit pyproject.toml version
git add pyproject.toml
git commit -m "Bump version to 0.9.0"
git checkout main
git pull origin main
git merge phase-9-dev
git push origin main
git tag v0.9.0
git push origin v0.9.0
```

## Pre-release Versions

Tags containing `-` are marked as prereleases on GitHub:

```bash
git tag v0.9.0-rc1
git push origin v0.9.0-rc1
# Creates a prerelease on GitHub
```

## CI Base Image Caching

CI caches a base image containing all Python dependencies to speed up builds:

- **Tag format:** `ghcr.io/doublegremlin181/potluck-base:cpu-<hash>`
- **Hash source:** SHA-256 of `pyproject.toml` + `uv.lock`
- **Cache hit:** ~2-3 min build (code changes only)
- **Cache miss:** ~8-10 min build (dependency changes trigger full rebuild)

If you change dependencies in `pyproject.toml`, expect the first CI run after the change to take longer.

## Troubleshooting

### Tag version mismatch

```
Error: Tag version (0.9.0) does not match pyproject.toml version (0.8.0)
```

The release workflow verifies the tag matches `pyproject.toml`. Fix by updating `pyproject.toml` before tagging:

```bash
git tag -d v0.9.0                 # Delete local tag
git push origin :refs/tags/v0.9.0 # Delete remote tag (if pushed)
# Fix pyproject.toml, commit, push
git tag v0.9.0
git push origin v0.9.0
```

### Disk space failures in CI

The release workflow frees disk space before building. If builds still fail with disk space errors, check if new dependencies significantly increased image size.

### Updating an existing release

If you need to re-release the same version (e.g., after a hotfix):

```bash
git tag -d v0.9.0
git push origin :refs/tags/v0.9.0
# Apply fix, commit, push to main
git tag v0.9.0
git push origin v0.9.0
```

This will overwrite the existing Docker images and GitHub release.
