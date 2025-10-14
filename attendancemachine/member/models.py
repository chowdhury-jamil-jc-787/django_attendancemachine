from django.conf import settings
from django.db import models

class Member(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    position = models.CharField(max_length=255, blank=True, default="")

    # OPTIONAL: many-to-many “view” using the pivot below
    users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through='MemberAssignment',
        related_name='members',
        blank=True,
    )

    class Meta:
        db_table = 'member'
        ordering = ['id']

    def __str__(self):
        return f'{self.name} <{self.email}>'

class MemberAssignment(models.Model):
    """Pivot table: auth_user <-> member"""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='member_assignments'
    )
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name='user_assignments'
    )
    # optional metadata fields
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_member'              # the pivot table name
        unique_together = ('user', 'member')  # prevent duplicate links
        indexes = [
            models.Index(fields=['user']),
            models.Index(fields=['member']),
        ]

    def __str__(self):
        return f'User {self.user_id} ↔ Member {self.member_id}'
