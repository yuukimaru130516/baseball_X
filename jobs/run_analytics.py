"""効果測定ジョブ（GitHub Actions / Cloud Run Jobs から呼び出す）。"""
import sys

from loguru import logger

from baseball_x.analytics.collector import collect_metrics


def main() -> None:
    logger.info("Analytics job started")
    collect_metrics()
    logger.info("Analytics job completed")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Analytics job failed")
        sys.exit(1)
