from django.conf import settings
from django.db import models


class Member(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    position = models.CharField(max_length=255, blank=True, default="")

    # M2M view (read-only convenience)
    users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="MemberAssignment",
        through_fields=("member", "user"),
        related_name="members",
        blank=True,
    )

    class Meta:
        db_table = "member"
        ordering = ["id"]

    def __str__(self):
        return f"{self.name} <{self.email}>"


class MemberAssignment(models.Model):
    """
    Pivot table: auth_user <-> member
    Each field is nullable so it can be cleared independently.
    Row is deleted only when ALL fields are NULL.
    """

    # ✅ MUST be nullable to allow "remove user only"
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="member_assignments",
    )

    # ✅ nullable
    member = models.ForeignKey(
        Member,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_assignments",
    )

    # ✅ nullable
    sign_in = models.ForeignKey(
        Member,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sign_in_assignments",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_member"
        # ❌ DO NOT use unique_together when fields are nullable
        # It breaks logic and causes unexpected duplicates
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["member"]),
            models.Index(fields=["sign_in"]),
        ]

    def __str__(self):
        return (
            f"Assignment {self.id} | "
            f"user={self.user_id} | "
            f"member={self.member_id} | "
            f"sign_in={self.sign_in_id}"
        )
