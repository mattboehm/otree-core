import queue
from django.conf import settings
import logging
from otree.models_concrete import (
    CompletedGroupWaitPage, CompletedSubsessionWaitPage,
)
import json
import traceback
import otree.common_internal
from otree.db.idmap import use_cache, save_objects
import threading
from otree.common_internal import get_views_module
from django.apps import apps
import channels
import redis_lock

logger = logging.getLogger('otree.waitpageworker')


class AfterAllPlayersArrive:
    def __init__(self, aapa_kwargs):
        self.set_non_model_attributes(aapa_kwargs)

    def try_to_complete(self) -> str:
        if self.was_already_completed():
            return ResponseCode.COMPLETE

        with use_cache():
            self.set_wait_page_attributes()
            return self.inner_try()

    def inner_try(self) -> str:
        self.complete_aapa()
        return ResponseCode.COMPLETE

    def set_non_model_attributes(self, kwargs):

        app_label = kwargs['app_label']

        app_config = apps.get_app_config(app_label)
        self.app_config = app_config
        app_name = app_config.name
        self.GroupClass = app_config.get_model('Group')
        self.SubsessionClass = app_config.get_model('Subsession')
        views_module = get_views_module(app_name)
        WaitPageClass = getattr(views_module, kwargs['page_name'])
        self.index_in_pages = kwargs['index_in_pages']
        self.player_id = kwargs.get('player_id')  # only for GBAT
        self.group_id = kwargs.get('group_id')
        self.group_id_in_subsession = kwargs.get('group_id_in_subsession')
        # for WaitPageCompletion
        self.subsession_id = kwargs['subsession_id']
        self.session_id = kwargs['session_id']
        self.wp = WaitPageClass()  # type: otree.views.abstract.WaitPage

    def set_wait_page_attributes(self):

        if self.group_id:
            group = self.GroupClass.objects.get(id=self.group_id)
        else:
            group = None
        subsession = self.SubsessionClass.objects.get(id=self.subsession_id)
        session = subsession.session

        # set attributes
        wp = self.wp
        wp.group = group
        wp.subsession = subsession
        wp.session = session
        wp.round_number = subsession.round_number
        wp._index_in_pages = self.index_in_pages
        wp._Constants = self.app_config.models_module.Constants
        wp.GroupClass = self.GroupClass
        wp.SubsessionClass = self.SubsessionClass

    def complete_aapa(self):
        wp = self.wp

        # the group membership might be modified
        # in after_all_players_arrive, so calculate this first
        participant_pk_set = set(
            wp._aapa_scope_object.player_set.values_list(
                'participant__pk', flat=True))

        wp._set_undefined_attributes()
        wp.after_all_players_arrive()
        save_objects()
        wp._mark_complete()
        wp.send_completion_message(participant_pk_set)

    def was_already_completed(self):
        if self.wp.wait_for_all_groups:
            already_completed = CompletedSubsessionWaitPage.objects.filter(
                page_index=self.index_in_pages,
                session_id=self.session_id
            ).exists()
        else:
            already_completed = CompletedGroupWaitPage.objects.filter(
                page_index=self.index_in_pages,
                id_in_subsession=self.group_id_in_subsession,
                session_id=self.session_id
            ).exists()
        return already_completed


class GroupByArrivalTime(AfterAllPlayersArrive):
    def inner_try(self) -> str:
        player_ids = self.wp._gbat_get_waiting_player_ids()
        regrouped = self.wp._gbat_try_to_regroup(player_ids)
        if regrouped:
            self.complete_aapa()
            return ResponseCode.COMPLETE
        elif regrouped == 3:
            return ResponseCode.NOT_COMPLETE

    def was_already_completed(self):
        group_id_in_subsession = self.GroupClass.objects.filter(
            player__id=self.player_id).values_list('id_in_subsession',
                                                   flat=True)[0]

        return CompletedGroupWaitPage.objects.filter(
            page_index=self.index_in_pages,
            id_in_subsession=group_id_in_subsession,
            session_id=self.session_id
        ).exists()


class WorkerBase:
    def try_to_complete(self, aapa_kwargs):
        if aapa_kwargs['group_by_arrival_time']:
            consumer = GroupByArrivalTime(aapa_kwargs)
        else:
            consumer = AfterAllPlayersArrive(aapa_kwargs)
        return consumer.try_to_complete()

    def listen(self):
        '''
        Wrap exceptions so that the process doesn't crash
        when there is an error, especially in the user's code.
        We wrap exceptions at this level rather than inside try_to_complete,
        because bots call that directly, and for bots we need to bubble up
        errors
        '''
        while True:
            aapa_kwargs = self.get_next_aapa_kwargs()
            try:
                response_code = self.try_to_complete(aapa_kwargs)
                response = {
                    'response_code': response_code,
                }
                self.respond(
                    response_key=aapa_kwargs['response_key'],
                    response=response
                )
            except Exception as exc:
                error_message = repr(exc)
                traceback_str = traceback.format_exc()
                logger.exception(error_message)
                response = {
                    'response_code': ResponseCode.ERROR,
                    'traceback': traceback_str,
                }
                self.respond(
                    response_key=aapa_kwargs['response_key'],
                    response=response
                )


    def get_next_aapa_kwargs(self):
        raise NotImplementedError()

    def respond(self, *, response_key, response):
        raise NotImplementedError()


class RedisWorker(WorkerBase):
    def __init__(self, redis_conn=None):
        self.redis_conn = redis_conn
        print('waitpageworker is listening for messages through Redis')

    def get_next_aapa_kwargs(self):
        # put it in a loop so that we can still receive KeyboardInterrupts
        # otherwise it will block
        while True:
            result = self.redis_conn.blpop(REDIS_REQUEST_KEY, timeout=3)
            if result is not None:
                break

        key, message_bytes = result
        aapa_kwargs = json.loads(message_bytes.decode('utf-8'))
        return aapa_kwargs

    def respond(self, *, response_key: str, response: dict):
        self.redis_conn.rpush(response_key, response)


class InProcessWorker(WorkerBase):
    def get_next_aapa_kwargs(self):
        # put it in a loop so that we can still receive KeyboardInterrupts
        # otherwise it will block
        while True:
            try:
                return request_queue.get(timeout=3)
            except queue.Empty:
                pass


class WaitPageThread(threading.Thread):
    def __init__(self):
        # daemon means that KeyboardInterrput will stop the process
        super().__init__(daemon=True)

    def run(self):
        logger.debug("Wait page worker thread running")
        InProcessWorker().listen()


REDIS_REQUEST_KEY = 'otree-wait-page-request'
REDIS_RESPONSE_KEY = 'otree-wait-page-response'

request_queue = queue.Queue()
response_queue = queue.Queue()

class ResponseCode:
    ERROR = 'ERROR'
    NOT_COMPLETE = 'NOT_COMPLETE'
    COMPLETE = 'COMPLETE'


def try_to_complete(aapa_kwargs):
    if getattr(otree.common_internal, 'USING_CLI_BOTS', False):
        InProcessWorker().try_to_complete(aapa_kwargs)
    elif otree.common_internal.USE_REDIS:
        redis_conn = otree.common_internal.get_redis_conn()
        redis_conn.rpush(REDIS_REQUEST_KEY, json.dumps(aapa_kwargs))
        response_key = aapa_kwargs['response_key']
        key, bytes = redis_conn.blpop(response_key)
        return json.loads(bytes.decode('utf-8'))
    else:
        request_queue.put(aapa_kwargs)
        # possible race condition: does the earlier call to get() always
        # get the next item? what if another thread is also calling get?
        # from the queue docs i think that 'multi-consumer' is supported
        # and locks are used.
        return response_queue.get()


def flush_redis(redis_conn):
    redis_conn.delete(REDIS_REQUEST_KEY)
    redis_conn.delete(REDIS_RESPONSE_KEY)
