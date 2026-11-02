"""Third-party code copied into this repo, not written here.

Everything under ``src/vendor/`` is someone else's source, kept byte-faithful to
its upstream release so it diffs cleanly, with its licence beside it. Each
package's ``__init__`` names the upstream version, why the copy exists, and the
issue that deletes it.

Do not edit vendored files to fix a bug or add a feature. Either the fix belongs
upstream, or it belongs in OUR code that calls the vendored module — the copy
stays a copy.
"""
