import logging

from auditer.utils.logging import setup_logging


class TestSetupLogging:
    def test_verbose_sets_debug(self):
        setup_logging(verbose=True)
        assert logging.getLogger().level == logging.DEBUG

    def test_default_sets_info(self):
        setup_logging(level="INFO", verbose=False)
        assert logging.getLogger().level == logging.INFO

    def test_httpx_suppressed(self):
        setup_logging()
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
