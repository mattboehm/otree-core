# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('otree', '0025_auto_20170910_1628'),
    ]

    operations = [
        migrations.CreateModel(
            name='FailedWaitPageExecution',
            fields=[
                ('id', models.AutoField(verbose_name='ID', primary_key=True, serialize=False, auto_created=True)),
                ('page_index', models.PositiveIntegerField()),
                ('group_id_in_subsession', models.PositiveIntegerField(null=True)),
                ('message', models.CharField(max_length=300)),
                ('traceback', models.TextField(default='')),
                ('session', models.ForeignKey(to='otree.Session')),
            ],
        ),
    ]
