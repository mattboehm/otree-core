from django.core.management import call_command
from django.test import TestCase
from django.test.client import RequestFactory

from otree import constants
from otree.models.session import Participant, SessionExperimenter

from tests.simple_game.views import MyPage
from tests.simple_game.models import Player
from tests.utils import capture_stdout


class Attribute(object):
    pass


class OTreeRequestFactory(RequestFactory):
    def __init__(self, *args, **kwargs):
        self.view_name = kwargs.pop('view_name')
        super(OTreeRequestFactory, self).__init__(*args, **kwargs)

    def request(self, **request):
        http_request = super(OTreeRequestFactory, self).request(**request)
        http_request.resolver_match = Attribute()
        http_request.resolver_match.url_name = self.view_name
        return http_request


class BaseViewTestCase(TestCase):
    view_name = 'tests.simple_game.views.Abstract'

    def setUp(self):
        self.factory = OTreeRequestFactory(view_name=self.view_name)
        self.request = self.factory.get('/my-page/')

        with capture_stdout():
            call_command('create_session', 'simple_game', 1)

        self.session_experimenter = SessionExperimenter.objects.first()
        self.participant = Participant.objects.first()
        self.player = Player.objects.first()

    def reload_objects(self):
        self.session_experimenter = SessionExperimenter.objects.get(
            pk=self.session_experimenter.pk
        )
        self.participant = Participant.objects.get(pk=self.participant.pk)
        self.player = Player.objects.get(pk=self.player.pk)


class TestPageView(BaseViewTestCase):
    def setUp(self):
        super(TestPageView, self).setUp()

        self.kwargs = {
            constants.session_user_code: self.participant.code,
            constants.user_type: 'p',
            constants.index_in_pages: 0,
        }
        self.view = MyPage.as_view()

        self.reload_objects()

    def test_status_ok(self):
        request = self.factory.get(
            '/{0}/{1}/shared/WaitUntilAssignedToGroup/0/'.format(
                self.kwargs[constants.user_type],
                self.kwargs[constants.session_user_code]))

        response = self.view(request, **self.kwargs)
        self.assertEqual(response.status_code, 200)

    def test_contains_form_field(self):
        request = self.factory.get(
            '/{0}/{1}/shared/WaitUntilAssignedToGroup/0/'.format(
                self.kwargs[constants.user_type],
                self.kwargs[constants.session_user_code]))

        self.player.add100_2 = 5
        self.player.save()

        response = self.view(request, **self.kwargs)
        self.assertContains(
            response, '''
                <input type="number" name="add100_1" required id="id_add100_1"
                 min="0" class="form-control">
            ''', html=True)
        self.assertContains(
            response, '''
                <input type="number" name="add100_2" required id="id_add100_2"
                 min="0" class="form-control" value="5">
            ''', html=True)

    def test_contains_custom_labels(self):
        request = self.factory.get(
            '/{0}/{1}/shared/WaitUntilAssignedToGroup/0/'.format(
                self.kwargs[constants.user_type],
                self.kwargs[constants.session_user_code]))

        response = self.view(request, **self.kwargs)
        self.assertContains(
            response, '''
                <label class="control-label" for="id_add100_1">
                    Your value here:
                </label>
            ''', html=True)
