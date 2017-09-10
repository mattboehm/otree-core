import queue
import logging
from otree.models_concrete import (
    CompletedGroupWaitPage, CompletedSubsessionWaitPage,
    FailedWaitPageExecution, FAILURE_MESSAGE_MAX_LENGTH
)
import json
import traceback
import otree.common_internal
from otree.db.idmap import use_cache, save_objects
import threading
from otree.common_internal import get_views_module
from django.apps import apps
import channels

logger = logging.getLogger('otree.waitpageworker')


class AfterAllPlayersArrive:
    def __init__(self, aapa_kwargs):
        self.set_non_model_attributes(aapa_kwargs)

    def try_to_complete(self):
        if self.was_already_completed():
            return

        with use_cache():
            self.set_wait_page_attributes()
            try:
                self.inner_try()
            except Exception as exc:
                error_message = repr(exc)
                traceback_str = traceback.format_exc()

                wp = self.wp
                channels_group = channels.Group(wp.get_channels_group_name())
                channels_group.send(
                    {
                        'text': json.dumps(
                            {
                                'error': error_message,
                                'traceback': traceback_str,
                            }
                        )
                    }
                )

                FailedWaitPageExecution.objects.create(
                    page_index=self.index_in_pages,
                    session_id=self.session_id,
                    group_id_in_subsession=self.group_id_in_subsession,
                    message=error_message[:FAILURE_MESSAGE_MAX_LENGTH],
                    traceback=traceback_str
                )
                raise

    def inner_try(self):
        self.complete_aapa()

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
    def inner_try(self):
        player_ids = self.wp._gbat_get_waiting_player_ids()
        regrouped = self.wp._gbat_try_to_regroup(player_ids)
        if regrouped:
            self.complete_aapa()

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
        consumer.try_to_complete()


class RedisWorker(WorkerBase):
    def __init__(self, redis_conn=None):
        self.redis_conn = redis_conn

    def listen(self):
        print('waitpageworker is listening for messages through Redis')
        while True:

            # blpop returns a tuple
            result = None

            # put it in a loop so that we can still receive KeyboardInterrupts
            # otherwise it will block
            while result is None:
                result = self.redis_conn.blpop(REDIS_REQUEST_KEY, timeout=3)

            key, message_bytes = result
            aapa_kwargs = json.loads(message_bytes.decode('utf-8'))
            self.try_to_complete(aapa_kwargs)


class InProcessWorker(WorkerBase):
    def listen(self):
        while True:
            aapa_kwargs = None

            # put it in a loop so that we can still receive KeyboardInterrupts
            # otherwise it will block
            while aapa_kwargs is None:
                try:
                    aapa_kwargs = request_queue.get(timeout=3)
                except queue.Empty:
                    pass

            self.try_to_complete(aapa_kwargs)


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


def try_to_complete(aapa_kwargs):
    if getattr(otree.common_internal, 'USING_CLI_BOTS', False):
        InProcessWorker().try_to_complete(aapa_kwargs)
    elif otree.common_internal.USE_REDIS:
        redis_conn = otree.common_internal.get_redis_conn()
        redis_conn.rpush(REDIS_REQUEST_KEY, json.dumps(aapa_kwargs))
    else:
        request_queue.put(aapa_kwargs)
