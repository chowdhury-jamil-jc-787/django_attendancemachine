
from django.contrib import admin, messages
from django.utils import timezone

from .models import DeviceSession


@admin.register(DeviceSession)
class DeviceSessionAdmin(admin.ModelAdmin):
    """
    Django Admin management for device login sessions.

    Administrators can:
    - View logged-in devices
    - Search and filter sessions
    - Check active/revoked status
    - Revoke one or multiple device sessions
    """

    list_display = (
        "user",
        "device_name",
        "ip_address",
        "created_at",
        "last_seen_at",
        "expires_at",
        "active_status",
        "revoked_at",
    )

    list_filter = (
        "revoked_at",
        "created_at",
        "expires_at",
    )

    search_fields = (
        "user__username",
        "user__email",
        "device_name",
        "ip_address",
        "user_agent",
    )

    readonly_fields = (
        "id",
        "user",
        "device_name",
        "user_agent",
        "ip_address",
        "created_at",
        "last_seen_at",
        "expires_at",
        "revoked_at",
        "active_status",
    )

    fields = (
        "id",
        "user",
        "device_name",
        "user_agent",
        "ip_address",
        "created_at",
        "last_seen_at",
        "expires_at",
        "revoked_at",
        "active_status",
    )

    ordering = (
        "-last_seen_at",
        "-created_at",
    )

    list_select_related = ("user",)

    list_per_page = 25

    date_hierarchy = "created_at"

    actions = [
        "revoke_selected_sessions",
    ]

    @admin.display(
        boolean=True,
        description="Active",
    )
    def active_status(self, obj):
        return obj.is_active

    @admin.action(
        description="Log out selected devices",
        permissions=["change"],
    )
    def revoke_selected_sessions(self, request, queryset):
        """
        Revoke the selected sessions immediately.

        The CustomJWTAuthentication class will reject
        subsequent requests using these sessions.
        """

        now = timezone.now()

        updated_count = queryset.filter(
            revoked_at__isnull=True
        ).update(
            revoked_at=now
        )

        if updated_count:
            self.message_user(
                request,
                f"{updated_count} device session(s) "
                "successfully logged out.",
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                "No active sessions were selected.",
                level=messages.INFO,
            )

    def has_add_permission(self, request):
        """
        Device sessions should only be created
        through the authentication system.
        """
        return False

    def has_delete_permission(self, request, obj=None):
        """
        Keep historical device sessions for auditing.
        Administrators should revoke rather than delete.
        """
        return False
