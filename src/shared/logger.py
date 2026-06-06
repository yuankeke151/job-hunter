import logging
from logging.handlers import RotatingFileHandler


def _setup() -> logging.Logger:
    from config import LOG_PATH

    fmt     = "%(asctime)s [%(levelname)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logger = logging.getLogger("job_hunter")
    if logger.handlers:
        return logger  # 已初始化，避免重复添加 handler

    logger.setLevel(logging.DEBUG)

    # 控制台：INFO 及以上
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter(fmt, datefmt))

    # 文件：DEBUG 及以上，自动轮转（5 MB × 3 份）
    fh = RotatingFileHandler(
        LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt, datefmt))

    logger.addHandler(sh)
    logger.addHandler(fh)
    return logger


log = _setup()
