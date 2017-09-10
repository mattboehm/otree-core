import logging
from django.core.management.base import BaseCommand
from otree.common_internal import get_redis_conn
from otree.waitpage import RedisWorker


class Command(BaseCommand):
    help = "oTree: Run the worker for browser bots."

    def handle(self, *args, **options):
        redis_conn = get_redis_conn()
        worker = RedisWorker(redis_conn)
        worker.listen()
