import os
import sys
import logging


base_path = os.path.dirname(os.path.abspath(__file__))
tests_path = os.path.join(base_path, "tests")

sys.path.insert(0, tests_path)
sys.path.insert(0, base_path)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")
os.environ.setdefault("DJANGO_COLORS", "nocolor")

default_test_apps = ('tests',)

loggers = ["otree", "raven"]


def runtests(argv):
    import django
    django.setup()

    from django.core.management.commands.test import Command

    class TestCommand(Command):
        def execute(self, *args, **options):
            import otree.common_internal
            otree.common_internal.USING_CLI_BOTS = True
            if not args:
                args = default_test_apps
            return super(TestCommand, self).execute(*args, **options)

    for name in loggers:
        logger = logging.getLogger(name)
        logger.setLevel(logging.CRITICAL)

    test_command = TestCommand()
    test_command.run_from_argv(argv[0:1] + ['test'] + argv[1:])


if __name__ == '__main__':
    runtests(sys.argv)
