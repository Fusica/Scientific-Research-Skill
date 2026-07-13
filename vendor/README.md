# Vendored upstream references

Each subdirectory is either a verbatim, license-preserving snapshot of selected upstream modules or a provenance-only notice. Vendored skills are not installed by default and should not be locally patched.

When adapting upstream behavior:

1. write the maintained behavior in the root `skills/` composition layer;
2. record scientific or runtime caveats there;
3. keep the upstream snapshot available for attribution and comparison;
4. update snapshots deliberately, never via an unreviewed bulk merge.

`selection_sha256` in `upstreams.lock.yaml` is computed by sorting every
selected file by its path relative to the vendor root, then hashing the
concatenation of `relative_path + NUL + sha256(file_bytes)`. Top-level
`LICENSE` and `UPSTREAM.md` metadata are excluded. The repository validator
also rejects any vendored file outside the declared selection.
