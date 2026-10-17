"""Keep OAuth codes, state and resource cursors out of Uvicorn access logs."""

import logging


class PrivateQueryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        arguments = record.args
        # Uvicorn supplies client, method, path-with-query, HTTP version and status.
        if isinstance(arguments, tuple) and len(arguments) == 5 and isinstance(arguments[2], str):
            record.args = (*arguments[:2], arguments[2].partition("?")[0], *arguments[3:])
        return True


def protect_access_logs() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, PrivateQueryFilter) for item in logger.filters):
        logger.addFilter(PrivateQueryFilter())
