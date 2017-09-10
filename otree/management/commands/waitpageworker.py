import logging
from django.core.management.base import BaseCommand
from otree.common_internal import get_redis_conn
import otree.waitpage


class Command(BaseCommand):
    help = "oTree: Run the worker for wait pages."

    def handle(self, *args, **options):
        redis_conn = get_redis_conn()
        otree.waitpage.flush_redis(redis_conn)
        worker = otree.waitpage.RedisWorker(redis_conn)
        worker.listen()
