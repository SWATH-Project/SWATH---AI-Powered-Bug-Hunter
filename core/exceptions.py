# core/exceptions.py
# Author      : Member 1
# Responsibility : Define ALL exceptions used across SWATH.
#                 Every file imports exceptions from here.
#                 Nobody creates exception classes anywhere else.
# ------------------------------------------------------------

# ── Base ─────────────────────────────────────────────────────────

class SWATHError(Exception):
    """
    Root exception for everything SWATH raises.
    Orchestrator uses this as the catch-all safety net.

    Hierarchy:
        SWATHError
            ├── DockerNotRunningError
            ├── BinaryNotFoundError
            ├── ToolTimeoutError
            ├── ToolExecutionError
            ├── EmptyOutputError
            ├── OutputParseError
            ├── OutOfScopeError
            └── BudgetExceededError
    """
    def __init__(self, message: str, tool: str = None):
        self.tool    = tool       # which tool caused it (optional)
        self.message = message
        super().__init__(message)

    def __str__(self):
        if self.tool:
            return f"[{self.tool}] {self.message}"
        return self.message


# ── Subprocess / Binary level ─────────────────────────────────────
# Raised by : BaseModule._run_subprocess()
# Caught by : Orchestrator._run_tool()
# These errors mean the tool could not run at all.

class DockerNotRunningError(SWATHError):
    """
    The SWATH Docker container is down or inaccessible.

    Example:
        raise DockerNotRunningError(
            "Docker container 'swath-kali' is not running."
        )

    Orchestrator action: abort scan immediately. Tools cannot run.
    """
    pass


class BinaryNotFoundError(SWATHError):
    """
    Tool binary is not installed or not in PATH.

    Example:
        subfinder not found → raise BinaryNotFoundError(
            "subfinder is not installed. Run scripts/install_tools.sh",
            tool="subfinder"
        )

    Orchestrator action: skip tool, log it, continue scan.
    """
    pass


class ToolTimeoutError(SWATHError):
    """
    Tool exceeded its allowed execution time.

    Example:
        subfinder hung for 300s → raise ToolTimeoutError(
            "subfinder timed out after 300s",
            tool="subfinder"
        )

    Orchestrator action: skip tool, log it, continue scan.
    """
    pass


class ToolExecutionError(SWATHError):
    """
    Tool ran but returned a non-zero exit code.
    Means the tool itself reported a failure.

    Example:
        subfinder exited with code 1 → raise ToolExecutionError(
            "subfinder exited with code 1. stderr: permission denied",
            tool="subfinder"
        )

    Orchestrator action: skip tool, log it, continue scan.
    """
    pass


# ── Output / Parsing level ────────────────────────────────────────
# Raised by : BaseModule._read_output_file() or each tool module
# Caught by : Each tool module (EmptyOutputError)
#             Orchestrator (OutputParseError)
# These errors mean the tool ran but output is missing or unreadable.

class EmptyOutputError(SWATHError):
    """
    Tool ran successfully but produced no output file,
    or the output file exists but is completely empty.

    This is often NORMAL — target may have no subdomains,
    no live hosts, no vulnerabilities found etc.

    Example:
        subfinder output file is empty → raise EmptyOutputError(
            "output/example.com/raw/subfinder.txt is empty",
            tool="subfinder"
        )

    Caught by: each tool module individually.
    Tool module decides: is empty output an error or a normal result?
    For subfinder: empty = no subdomains found = return [] (not a crash)
    """
    pass


class OutputParseError(SWATHError):
    """
    Tool produced output but it could not be parsed.
    Usually means unexpected format — e.g. expected JSON, got HTML.

    Example:
        httpx returned malformed JSON → raise OutputParseError(
            "httpx output is not valid JSON: line 14 col 3",
            tool="httpx"
        )

    Orchestrator action: skip tool, log it, continue scan.
    """
    pass


# ── Scope level ───────────────────────────────────────────────────
# Raised by : ScopeEnforcer.check()
# Caught by : swath.py (before scan even starts)
# This is the most critical exception — stops everything.

class OutOfScopeError(SWATHError):
    """
    Target domain is outside the defined bug bounty scope.

    Example:
        google.com found via redirect → raise OutOfScopeError(
            "google.com is outside example.com scope",
            tool="scope_enforcer"
        )

    Action: abort scan immediately. Never send traffic to OOS target.
    """
    pass


# ── Budget level ──────────────────────────────────────────────────
# Raised by : BudgetTracker
# Caught by : Orchestrator gate check

class BudgetExceededError(SWATHError):
    """
    Scan has consumed its entire request or time budget.

    Example:
        raise BudgetExceededError(
            "Budget exhausted: 3842/3842 requests used"
        )

    Orchestrator action: skip all remaining phases, generate report.
    """
    pass