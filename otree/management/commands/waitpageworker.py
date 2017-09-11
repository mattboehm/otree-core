import logging
from django.core.management.base import BaseCommand
from otree.common_internal import get_redis_conn
import otree.waitpage


class Command(BaseCommand):
    help = "oTree: Run the worker for wait pages."

    def handle(self, *args, **options):
        import redis_lock
        redis_conn = get_redis_conn()
        lock = redis_lock.Lock(
            redis_conn, name='wait_page_worker', expire=2,
            auto_renewal=True
        )
        if not lock.acquire(timeout=2):
            raise Exception('Another wait page worker is already running')
        otree.waitpage.flush_redis(redis_conn)
        worker = otree.waitpage.RedisWorker(redis_conn)
        worker.listen()
