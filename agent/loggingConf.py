import json
import logging
import sys
from datetime import datetime


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "ts": datetime.utcnow().isoformat() + "Z",
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
        )


def configure_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]
