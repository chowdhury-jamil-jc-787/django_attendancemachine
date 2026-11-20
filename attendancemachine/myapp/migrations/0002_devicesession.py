
# Generated for Django 5.2
# Device Session Management Migration

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('myapp', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DeviceSession',
            fields=[
                (
                    'id',
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    )
                ),
                (
                    'device_name',
                    models.CharField(
                        default='Unknown device',
                        max_length=160,
                    )
                ),
                (
                    'user_agent',
                    models.CharField(
                        blank=True,
                        max_length=512,
                    )
                ),
                (
                    'ip_address',
                    models.GenericIPAddressField(
                        blank=True,
                        null=True,
                    )
                ),
                (
                    'created_at',
                    models.DateTimeField(
                        default=django.utils.timezone.now,
                    )
                ),
                (
                    'last_seen_at',
                    models.DateTimeField(
                        default=django.utils.timezone.now,
                    )
                ),
                (
                    'expires_at',
                    models.DateTimeField()
                ),
                (
                    'revoked_at',
                    models.DateTimeField(
                        blank=True,
                        null=True,
                    )
                ),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='device_sessions',
                        to=settings.AUTH_USER_MODEL,
                    )
                ),
            ],
            options={
                'ordering': [
                    '-last_seen_at',
                    '-created_at',
                ],
                'indexes': [
                    models.Index(
                        fields=['user', 'revoked_at'],
                        name='device_user_active_idx',
                    ),
                ],
            },
        ),
    ]
