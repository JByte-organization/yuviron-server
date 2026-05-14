from __future__ import annotations

from dataclasses import dataclass, field


ERROR = "ERROR"
WARN = "WARN"


@dataclass(frozen=True)
class Finding:
    severity: str
    check: str
    message: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def error(self, check: str, message: str) -> None:
        self.findings.append(Finding(ERROR, check, message))

    def warn(self, check: str, message: str) -> None:
        self.findings.append(Finding(WARN, check, message))

    @property
    def errors(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == WARN]
