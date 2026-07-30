# Capture App Coding Rules & Standards

Current documentation refresh: 2026-05-25.

## Python (Backend)

### Flask Routes
- **Every non-auth route** must have `@login_required` decorator
- Use `request.form.get('param', '')` for POST data, `request.args.get('param', '')` for query params
- Return JSON via `jsonify()` for API endpoints, `render_template()` for pages
- Always validate QR code format (10-digit numeric or `TEMP-XXXXX`) before database operations

### Database Access
- Always use `sqlite3.connect()` with explicit `conn.close()` in a `try/finally`
- Use the `_open_db()` helper when connecting to `QR_codes.db`
- Never hardcode paths â€” use `os.getenv("QR_CODES_DB_PATH", default_path)`
- Handle both Linux (`/home/developer/...`) and Windows paths via `os.getenv()` fallbacks
- Use parameterized queries (`?` placeholders) â€” never concatenate user input into SQL
- Column names with spaces must be quoted: `"Building Code"`, `"QR_code_ID"`
- Use `ensure_column()` pattern before inserting into columns that may not exist yet

### Error Handling
- Wrap all DB and file operations in `try/except`
- Log errors with descriptive emoji prefixes: `print("ðŸ”´ ERROR: ...")`, `print("âš ï¸ WARNING: ...")`
- Return `jsonify({"error": "description"})` with appropriate HTTP status codes
- Never expose raw SQL errors or tracebacks to the client

### File Operations
- Filenames follow: `<QR> <Building> <AssetType> - <Seq>.jpg`
- Sequence ranges: ME `0..4` (Extra Photo at `4`), BF `0..3` (Extra at `3`), EL `0..3` (Extra at `3`). The submit loop iterates the full range; missing seqs are skipped by the existing `continue` guard.
- Always sanitize components via `sanitize_component()` â€” strip non-alphanumeric, truncate
- Use `save_image_file()` for uploads â€” it re-encodes JPEGs via Pillow and applies `ImageOps.exif_transpose()` to honor EXIF Orientation before writing; the raw-bytes branch still catches corrupt-image cases
- When renaming files, always create backups first, rename atomically, rollback on failure

---

## HTML / CSS (Frontend)

### Template Structure
- `base.html` provides the common `<head>` with CSS links â€” extend it
- `start.html` is the landing page â€” heavy JavaScript for QR scanning and form logic
- `capture.html` contains the camera modal and gallery â€” most JS is inline
- Never duplicate CSS/JS imports already present in `base.html`

### CSS Rules
- **Utility-class approach** in `styles.css` â€” prefer existing classes over new ones
- Common patterns: `flex-col`, `w-full`, `rounded-lg`, `bg-blue-50`, `text-white`
- Component styles go in `ui-components.css`
- Logo-specific styles in `logo.css`
- **Never use inline styles** except for dynamic values (e.g., computed transforms)
- Use `max-width: none` on inputs that are absolutely positioned

### Mobile-First Design
- Always test layouts on mobile viewports (375px width minimum)
- Touch-friendly targets: minimum **44px Ã— 44px**
- Handle orientation changes (portrait preferred for camera)
- Use `safe-area-inset-*` for devices with notches
- Camera controls must have large, easily tappable buttons

---

## JavaScript (Frontend)

### General
- **Vanilla ES6+ only** â€” no frameworks (React, Vue, etc.)
- Use `fetch()` for API calls, always handle errors with `.catch()` or `try/catch` with `async/await`
- Use `FormData` for file uploads with `multipart/form-data`

### Camera Interface
- Always check for HTTPS/localhost before requesting camera access
- Use `navigator.mediaDevices.getUserMedia()` for video stream
- Prefer `ImageCapture` API for photos; fall back to `<canvas>` capture
- Handle `NotAllowedError` (permission denied) and `NotFoundError` (no camera) gracefully
- Always stop media tracks when leaving the camera view: `stream.getTracks().forEach(t => t.stop())`
- Zoom control: use `track.applyConstraints({ advanced: [{ zoom: value }] })`

### QR Code Handling
- Validate QR codes client-side: exactly 10 digits (`/^\d{10}$/`)
- Check server-side via `/api/check-qr?qr=XXXXXXXXXX` before proceeding
- Handle the "QR already captured" flow with a confirmation modal
- Temporary codes: request from `/api/get-temp-code`, format is `TEMP-XXXXX`

### Form State
- Persist form state (building, location, asset type) to `sessionStorage` for the duration of a capture session
- Clear state on successful submission or when starting a new session
- Listen for `beforeunload` to warn about unsaved photos

---

## File Naming Conventions

| Type | Convention | Example |
|------|-----------|---------|
| Python module | `snake_case.py` | `parameter_update_service.py` |
| Template | `snake_case.html` | `capture.html` |
| CSS file | `snake_case.css` or `kebab-case.css` | `ui-components.css` |
| JS file | `snake_case.js` | `start.js` |
| Uploaded photos | `<QR> <Building> <Type> - <Seq>.jpg` | `0000177276 314-1 ME - 0.jpg` |
| JSON output | `<QR>_et.json` | `0000177276_et.json` |
| Processing logs | `processed_images*.log` | `processed_images_el.log` |

---

## Git & Deployment

- Never commit `venv/`, `__pycache__/`, `.env` files, `data/*.db-wal`, `data/*.db-shm`, or `logs/`
- Keep `requirements.txt` updated when adding new Python packages
- Test on both Windows (local dev) and Ubuntu (production server)
- Production runs via `gunicorn` behind Nginx; dev uses `app.run(debug=True)`
- Run `sqlite_checkpoint.sh` periodically to merge WAL files on production

---

## Security Checklist

- [ ] All main routes have `@login_required`
- [ ] No raw SQL concatenation â€” parameterized queries only
- [ ] No sensitive data (API keys, passwords) in source code â€” use `.env`
- [ ] QR code input validated (10-digit numeric or `TEMP-XXXXX` format)
- [ ] Uploaded filenames are server-generated, not user-provided
- [ ] File paths validated â€” no path traversal via user input
- [ ] Camera access only over HTTPS/localhost
- [ ] CSRF protection on all POST routes
