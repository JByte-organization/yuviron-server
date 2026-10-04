# =============================================================================
# scripts/core/findings.py — Модель для сбора результатов аудита/диагностики.
#
# Используется командами security audit и doctor:
# вместо немедленного вывода ошибок, все находки собираются в Report,
# затем выводятся сводкой и определяют код завершения.
#
# Пример использования:
#   report = Report()
#   report.error("tls-files", "cert file is missing")
#   report.warn("container-hardening", "read_only not set")
#   if report.errors:
#       sys.exit(1)
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass, field


ERROR = "ERROR"   # критическая проблема — команда завершится с ошибкой
WARN = "WARN"     # предупреждение — команда завершится успешно (если не --strict)


@dataclass(frozen=True)
class Finding:
    """Одна находка аудита или диагностики."""
    severity: str   # ERROR или WARN
    check: str      # имя проверки (например "tls-files", "container-hardening")
    message: str    # описание проблемы


@dataclass
class Report:
    """Коллекция находок для одного запуска аудита."""
    findings: list[Finding] = field(default_factory=list)

    def error(self, check: str, message: str) -> None:
        """Добавить критическую ошибку."""
        self.findings.append(Finding(ERROR, check, message))

    def warn(self, check: str, message: str) -> None:
        """Добавить предупреждение."""
        self.findings.append(Finding(WARN, check, message))

    @property
    def errors(self) -> list[Finding]:
        """Только ошибки (severity == ERROR)."""
        return [finding for finding in self.findings if finding.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        """Только предупреждения (severity == WARN)."""
        return [finding for finding in self.findings if finding.severity == WARN]
