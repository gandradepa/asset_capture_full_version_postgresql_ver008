# Project skills

These skills are checked into the repository so Codex can discover them from
`.agents/skills` for every contributor working in this project.

## Sources

| Skill set | Upstream | Pinned commit | Installed paths |
| --- | --- | --- | --- |
| Context7 CLI | <https://github.com/upstash/context7> | `594a73133e14631af8c915a1b4f2c8039c964fe1` | `context7-cli/` |
| Frontend Design | <https://github.com/anthropics/skills> | `b29e7cf65e5cb78a5ac33d582270551bc74a14eb` | `frontend-design/` |
| Superpowers | <https://github.com/obra/superpowers> | `44c9b2d6e889982ac18c27d05a19fefe335194e1` | The remaining skill directories in this folder |

Installed on 2026-08-03. Update these copies deliberately from reviewed
upstream commits; do not replace them from an unpinned branch without reviewing
the resulting diff.

Upstream license copies are kept in `../LICENSES/`. Frontend Design carries its
own `LICENSE.txt` inside the skill directory.

## Runtime notes

- Context7 uses `npx ctx7@latest` and works without authentication for normal
  documentation lookups. `CONTEXT7_API_KEY` is optional for higher limits.
- Frontend Design is guidance-only and has no runtime dependencies.
- Superpowers is a coordinated set of skills. Keep the full set together because
  individual workflows reference one another.
