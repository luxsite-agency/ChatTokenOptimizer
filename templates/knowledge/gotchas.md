# Gotchas: what cost time and how it was fixed

One entry per trap. The heading carries the date and the symptom in the words you saw it
(that is what search will find). Then the cause and the fix with commands. No story of how you got there.
No passwords, keys or tokens: only where the access is kept.

## 2026-01-15 — `npm test` passes locally, fails in CI with "Cannot find module"
Cause: a file imported with the wrong letter case; macOS is case-insensitive, CI is Linux.
Fix: `git mv Button.jsx button.jsx` and fix the import; `git config core.ignorecase false`.
