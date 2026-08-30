# SWATH Changelog

All notable changes to this project will be documented in this file.

## [1.0.1] - 2026-08-18 - Production Hardening

### Fixed
- **CRITICAL**: Fixed `IndentationError` in `core/plugin_loader.py` that prevented the entire orchestrator from loading
- **CRITICAL**: Fixed hardcoded `/home/huntforge/` path references in `modules/secrets/gitleaks.py` and `scripts/installer.py` to use correct `/home/swath/` paths
- **HIGH**: Fixed output directory creation bug in `modules/discovery/httpx.py` where `os.makedirs` was called after returning early
- **HIGH**: Fixed `FileNotFoundError` in `core/database.py` when using in-memory databases (`:memory:`)
- **MEDIUM**: Fixed `str.join()` type error in `core/siem_formatter.py` CEF formatter
- **MEDIUM**: Fixed Windows PATH separator issues in `scripts/installer.py` (colon `:` → `os.pathsep`)
- **MEDIUM**: Fixed class name `HuntForgeInstaller` → `SWATHInstaller` in `scripts/installer.py`
- **MEDIUM**: Fixed all `~/.huntforge/` references to `~/.swath/` in installer and documentation

### Added
- **NEW**: Pre-flight tool availability check in `OrchestratorV2.preflight_tool_check()` - warns about missing tools before scan starts
- **NEW**: Automatic detection of Docker vs local execution context in preflight check
- **NEW**: API-based tools (crtsh, graphql_voyager) correctly identified as always available
- **NEW**: `.gitignore` updated to exclude `swath/` virtual environment directory

### Changed
- **BREAKING**: Default budget `action_on_exceeded` changed from `"warn"` to `"abort"` - scans will now stop when request budget is exceeded
- **IMPROVED**: Dockerfile tool installation now logs failures clearly instead of silently continuing
- **IMPROVED**: Dockerfile verification step now checks all critical tools individually
- **IMPROVED**: Budget tracker warnings are now more visible in scan output

### Security
- Fixed path traversal risk in gitleaks source directory resolution
- Added container name consistency (`swath-kali` used everywhere)

## [1.0.0] - 2026-07-24 - Initial Release

- 7-phase reconnaissance pipeline
- 19 integrated security tools
- AI-powered methodology generation
- Executive report generation
- Interactive Metasploit-style console
- Continuous monitoring with diff engine
- Multi-channel notifications
- SQLite persistence layer
- Docker isolation
- Budget and resource tracking
