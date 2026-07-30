# API Coding Rules & Standards

Current documentation refresh: 2026-05-25.

## Python (Extraction Scripts)

### Architecture Pattern
- **Every extraction script** must follow the `AssetProcessor` OOP pattern with `__init__`, `discover_assets()`, `process_single_asset()`, and `run()` methods
- **Configuration** must be centralized in a `Config` class â€” no loose global variables
- **Pydantic models** (`*StructuredExtraction`) enforce strict JSON schema from LLM output with `extra="forbid"` and `str_strip_whitespace=True`
- **Entry point** must use `if __name__ == "__main__":` with `try/except` around `setup_environment()` and `processor.run()`

### Shared Validators (`validators_shared.py`)
- **DRY Principle**: All field normalization functions MUST live in `validators_shared.py` â€” never duplicate logic in individual scripts
- When adding a new normalizer, import it in every script that needs it via the `try/except ImportError` pattern with local fallbacks
- Normalizers must return empty string `""` on invalid input â€” never `None`, never raise exceptions
- String outputs must be capped to a maximum length (e.g., 64 for serial, 80 for model, 120 for description)
- `looks_like_date_misread_serial(serial, year_hint="")` is the shared detector for date-shaped serial misreads (incl. upside-down dates like `8102/90` = `09/2018` rotated). ME wires it into `_is_serial_candidate()` (rejection auto-triggers the reread + OCR rescue); BF blanks the candidate serial so the heavier-model fallback fires. Both add the `serial_date_misread_suspected` reason code and cap Serial confidence at 65 when suspected.

### Completeness Guard
- An asset is **SKIPPED** (not saved) only if the existing JSON has a **strictly higher** completeness score
- If the new score is **equal to or greater than** the existing score, the asset is **UPDATED** (overwritten)
- Even when skipping a save, always call `_update_ai_status(qr)` to prevent infinite reprocessing
- Completeness score = `(non-empty required fields / total required fields) Ã— 100`

### OCR & Image Processing (ver04 Standards)
- All scripts must support the **Hybrid OCR** approach: Tesseract provides context text that is injected into the LLM prompt
- OCR mode must be configurable via environment variable (`*_OCR_MODE`) with values: `off`, `light`, `full`
- `light` mode (recommended): Only run OCR when LLM output for a field is weak or missing
- `full` mode: Always run OCR enrichment regardless of LLM confidence
- **Digital Rotation**: Preprocessing MUST include 90-degree rotations for ALL primary image sequences (e.g., ME: 0, 1, 3) to support vertical tag reading
- **Hallucination Defense (ver04)**:
    - **UBC Tag Regex**: Apply strict digit-enforcement regex to discard pure-alpha hallucinations (e.g. "BEA-PEE").
    - **Serial Negative Prompts**: Use guardrails to prevent common field-bleeding (e.g., extracting "ORDER NO." as Serial Number).
    - **Local Normalizers**: Scripts should implement local `_local_normalize_*` helpers to preserve slashes/spaces (preventing flattening of codes like 15/208/1).

### Concurrency
- Use `ThreadPoolExecutor` with `MAX_WORKERS = 8` for parallel asset processing
- Never exceed 8 concurrent threads to avoid OpenAI rate limits
- Use `as_completed()` for result collection â€” not `map()`

---

## Database Access

### Connection Patterns
- Always use `with closing(sqlite3.connect(path)) as conn:` context manager
- Use `closing()` on cursors as well: `with closing(conn.cursor()) as cur:`
- Never hardcode database paths â€” use `Config.DB_PATH` resolved from `DEV_PATH` env variable
- Use parameterized queries (`?` placeholders) to prevent SQL injection

### Schema Resilience
- Wrap column-specific queries in `try/except sqlite3.OperationalError` for schema version tolerance
- Use `PRAGMA table_info()` to dynamically resolve column names when schema may vary
- Implement `_resolve_column()` pattern to match column names across schema versions

### Status Management
- `ai_status=1` means "AI has processed this QR" â€” prevents reprocessing
- `Approved=1` or `Flagged=1` in `sdi_dataset` means "human reviewed" â€” always skip these
- Always mark `ai_status=1` after processing, even on failure, to prevent infinite retry loops

---

## Error Handling

### API Calls (OpenAI)
- Retry on `APIConnectionError`, `RateLimitError`, and `APIStatusError` with configurable `API_MAX_RETRIES` and `API_RETRY_DELAY`
- Catch `BadRequestError` separately â€” do NOT retry (indicates malformed request)
- Catch `ValidationError` from Pydantic parsing â€” log and return `None`, don't crash the batch

### File Operations
- Always use `encoding="utf-8"` for JSON reads/writes
- Use `os.makedirs(path, exist_ok=True)` before any write operation
- Catch `json.JSONDecodeError` when reading existing output files

### General Rules
- Never let a single asset failure crash the entire batch â€” catch per-asset in the `ThreadPoolExecutor` loop
- Log errors with `logging.error()` and include `exc_info=True` for tracebacks
- Use `logging.critical()` only for unrecoverable startup failures

---

## Logging Standards

### Console Output Format
- Summary line: `Total assets processed: {N}\nSuccessfully saved: {M}`
- Per-asset line: `- QR: {qr} | {TYPE} | {TIMESTAMP} | Building: {building}`
- **Do NOT** list skipped assets in the final summary
- Suppress verbose `httpx` and `openai` loggers: `logging.getLogger("httpx").setLevel(logging.WARNING)`

### Log Levels
| Level | Usage |
|-------|-------|
| `INFO` | Asset discovery, OCR mode, save confirmations |
| `WARNING` | Fallback validators, schema mismatch, DB column missing |
| `ERROR` | API failures, file I/O errors, single-asset failures |
| `CRITICAL` | Script cannot start (missing API key, missing venv) |

---

## File Naming Conventions

| Type | Pattern | Example |
|------|---------|---------|
| Input images | `<QR> <Building> <Type>-<Seq>.jpg` | `0000183710 100 EL-1.jpg` |
| Output JSON | `<QR>_<Type>_<Building>.json` | `0000183710_EL_100.json` |
| Env files | `OpenAI_key_<user>.env` | `OpenAI_key_giba.env` |
| Shell scripts | `snake_case.sh` | `auto_process_assets.sh` |
| Python scripts | `API_interface_<TYPE>_ver00.py` | `API_interface_ME_ver00.py` |

### Image Filename Regex (per script)

Each pipeline tunes its sequence character class to discover the full per-discipline range, while `VALID_SUFFIXES` remains the actual gate for what reaches the LLM.

| Script | `FILENAME_PATTERN` sequence class | `VALID_SUFFIXES` |
|---|---|---|
| `API_interface_ME_ver00.py` | `[0-4]` | `{"0", "1", "3"}` |
| `API_interface_BF_ver00.py` | `[0-3]` | `{"0", "1"}` |
| `API_interface_EL_ver00.py` | `[0-3]` | `{"0", "1", "2"}` |

Groups: `QR_code`, `Building_code`, `Asset_type`, `Sequence_number`.

The optional **Extra Photo** sequence (ME `-4`, BF `-3`, EL `-3`) is discovered by the regex so it can be logged as `invalid_seq` rather than the noisier `name_mismatch`, but it is intentionally absent from `VALID_SUFFIXES` and is therefore never added to `info["images"]` or shown to the LLM.

---

## Security

- **API Keys**: Store in `.env` files only â€” never commit to source control
- **Database paths**: Resolve from environment variables, never hardcode
- Never expose raw API error messages to end users or logs with API keys
- `.env` files and `__pycache__/` must be in `.gitignore`

---

## Cross-Platform Compatibility

- Use `platform.system()` to detect OS and configure Tesseract path accordingly
- Windows: Check `LOCALAPPDATA`, `Program Files`, and `PATH` for `tesseract.exe`
- Linux: Use `shutil.which("tesseract")` or fallback to `"tesseract"`
- Paths: Always use `os.path.join()` â€” never hardcode path separators
