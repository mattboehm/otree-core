# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('otree', '0024_auto_20170828_0419'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='completedgroupwaitpage',
            name='fully_completed',
        ),
        migrations.RemoveField(
            model_name='completedsubsessionwaitpage',
            name='fully_completed',
        ),
    ]
