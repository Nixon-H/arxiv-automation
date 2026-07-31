class AutomationError(Exception):
    pass


class ConfigValidationError(AutomationError):
    pass


class DataParserError(AutomationError):
    pass


class SafetyLockoutError(AutomationError):
    pass


class SmtpTransmissionError(AutomationError):
    pass


class AccountHealthError(AutomationError):
    pass


class IntegrityCheckError(AutomationError):
    pass


class TemplateRenderError(AutomationError):
    pass


class ReportExportError(AutomationError):
    pass


class RateLimitError(AutomationError):
    pass


class DnsValidationError(AutomationError):
    pass


class FileLockError(AutomationError):
    pass


class PluginError(AutomationError):
    pass


class BounceError(AutomationError):
    pass
