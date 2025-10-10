from django.db import IntegrityError
from django.contrib.auth import get_user_model
from django.db.models import Prefetch

from rest_framework import viewsets, status, decorators
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Member, MemberAssignment
from .serializers import MemberSerializer, MemberAssignmentSerializer
from .pagination import MemberPagination


User = get_user_model()


class ResponseMixin:
    def ok(self, message="", payload=None, status_code=status.HTTP_200_OK):
        body = {"success": True, "message": message}
        if isinstance(payload, dict):
            body.update(payload)
        return Response(body, status=status_code)

    def fail(self, message="Validation error.", errors=None, status_code=status.HTTP_400_BAD_REQUEST):
        body = {"success": False, "message": message}
        if errors is not None:
            body["errors"] = errors
        return Response(body, status=status_code)


class MemberViewSet(ResponseMixin, viewsets.ModelViewSet):
    """
    CRUD for Member with uniform success/fail responses.
    Also provides:
      - POST /api/assign/members/{id}/assign-user/    {"user_id": <int>}
      - POST /api/assign/members/{id}/unassign-user/  {"user_id": <int>}
      - GET  /api/assign/members/{id}/users/
      - POST /api/assign/members/{id}/delete/         (server-safe delete)
      - POST /api/assign/members/{id}/update/         (server-safe PUT)
      - POST /api/assign/members/{id}/patch/          (server-safe PATCH)
    """
    queryset = Member.objects.all().order_by('id')
    serializer_class = MemberSerializer
    permission_classes = [AllowAny]
    pagination_class = MemberPagination  # list() uses this to wrap response

    # RETRIEVE
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        data = self.get_serializer(instance).data
        return self.ok("Member fetched successfully.", {"member": data})

    # CREATE
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return self.fail("Could not create member.", serializer.errors)
        self.perform_create(serializer)
        return self.ok("Member created successfully.", {"member": serializer.data}, status.HTTP_201_CREATED)

    # UPDATE (PUT)
    def update(self, request, *args, **kwargs):
        partial = False
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        if not serializer.is_valid():
            return self.fail("Could not update member.", serializer.errors)
        self.perform_update(serializer)
        return self.ok("Member updated successfully.", {"member": serializer.data})

    # PARTIAL UPDATE (PATCH)
    def partial_update(self, request, *args, **kwargs):
        partial = True
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        if not serializer.is_valid():
            return self.fail("Could not update member.", serializer.errors)
        self.perform_update(serializer)
        return self.ok("Member updated successfully.", {"member": serializer.data})

    # DELETE (standard)
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        member_id = instance.id
        try:
            self.perform_destroy(instance)
        except IntegrityError:
            return self.fail(
                "Cannot delete member due to related records.",
                status_code=status.HTTP_409_CONFLICT
            )
        return self.ok(f"Member {member_id} deleted successfully.", {"id": member_id})

    # --------- EXTRA ACTIONS: server-safe POST aliases for blocked verbs ---------

    @decorators.action(detail=True, methods=['post'], url_path='delete', permission_classes=[AllowAny])
    def delete_via_post(self, request, *args, **kwargs):
        """
        Server-safe delete for hosts that block DELETE.
        POST /api/assign/members/{id}/delete/
        """
        return self.destroy(request, *args, **kwargs)

    @decorators.action(detail=True, methods=['post'], url_path='update', permission_classes=[AllowAny])
    def update_via_post(self, request, *args, **kwargs):
        """
        Server-safe PUT for hosts that block PUT.
        POST /api/assign/members/{id}/update/
        Body: same as PUT.
        """
        return self.update(request, *args, **kwargs)

    @decorators.action(detail=True, methods=['post'], url_path='patch', permission_classes=[AllowAny])
    def patch_via_post(self, request, *args, **kwargs):
        """
        Server-safe PATCH for hosts that block PATCH.
        POST /api/assign/members/{id}/patch/
        Body: same as PATCH.
        """
        return self.partial_update(request, *args, **kwargs)

    # --------- EXTRA ACTIONS: pivot (auth_user <-> member) ---------

    @decorators.action(detail=True, methods=['post'], url_path='assign-user', permission_classes=[AllowAny])
    def assign_user(self, request, pk=None):
        """
        Link a user to this member via the pivot.
        Body: { "user_id": 5 }
        """
        member = self.get_object()
        user_id = request.data.get('user_id')
        if not user_id:
            return self.fail("user_id is required.")
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return self.fail("User not found.", status_code=status.HTTP_404_NOT_FOUND)

        try:
            link, created = MemberAssignment.objects.get_or_create(user=user, member=member)
        except IntegrityError:
            return self.fail("Could not assign user due to an integrity error.", status_code=status.HTTP_409_CONFLICT)

        data = MemberAssignmentSerializer(link).data
        msg = "User assigned to member." if created else "User already assigned to member."
        return self.ok(msg, {"assignment": data})

    @decorators.action(detail=True, methods=['post'], url_path='unassign-user', permission_classes=[AllowAny])
    def unassign_user(self, request, pk=None):
        """
        Remove a user<->member link.
        Body: { "user_id": 5 }
        """
        member = self.get_object()
        user_id = request.data.get('user_id')
        if not user_id:
            return self.fail("user_id is required.")

        deleted, _ = MemberAssignment.objects.filter(user_id=user_id, member=member).delete()
        if deleted:
            return self.ok("User unassigned from member.", {"user_id": int(user_id), "member_id": member.id})
        return self.fail("Assignment did not exist.", status_code=status.HTTP_404_NOT_FOUND)

    @decorators.action(detail=True, methods=['get'], url_path='users', permission_classes=[AllowAny])
    def list_users(self, request, pk=None):
        """
        List user IDs (and optional pivot records) linked to this member.
        """
        member = self.get_object()
        assignments = (
            MemberAssignment.objects
            .filter(member=member)
            .select_related('user')
            .order_by('id')
        )
        ser = MemberAssignmentSerializer(assignments, many=True)
        user_ids = [a['user_id'] for a in ser.data]  # from PrimaryKeyRelatedField
        return self.ok("Users linked to member fetched successfully.", {
            "user_ids": user_ids,
            "assignments": ser.data
        })


class UserMembersView(ResponseMixin, APIView):
    """
    Vice-versa endpoint:
      GET /api/assign/users/<user_id>/members/?page=1&perPage=10
    Returns members linked to the given user, with the same pagination envelope.
    """
    permission_classes = [AllowAny]
    pagination_class = MemberPagination

    def get(self, request, user_id: int):
        # Validate user
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return self.fail("User not found.", status_code=status.HTTP_404_NOT_FOUND)

        # Members linked to this user (via pivot)
        qs = (
            Member.objects.filter(user_assignments__user=user)
            .order_by('id')
            .distinct()
        )

        # Paginate manually to keep consistent envelope/message
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = MemberSerializer(page, many=True).data

        return Response({
            "success": True,
            "message": "Members for user fetched successfully.",
            "members": data,
            "pagination": {
                "page": paginator.page.number,
                "total": paginator.page.paginator.count,
                "perPage": paginator.get_page_size(request),
            }
        }, status=status.HTTP_200_OK)


class UsersMembersView(APIView):
    """
    GET /api/assign/users/members/?page=1&perPage=10
    Optional: ?user_ids=1,2,3
    Returns users with their linked members (paginated by users).
    """
    permission_classes = [AllowAny]
    pagination_class = MemberPagination

    def get(self, request):
        raw_ids = request.query_params.get('user_ids', '').strip()
        ids = []
        if raw_ids:
            try:
                ids = [int(x) for x in raw_ids.split(',') if x.strip()]
            except ValueError:
                return Response(
                    {"success": False, "message": "user_ids must be a comma-separated list of integers."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        qs = User.objects.all().order_by('id')
        if ids:
            qs = qs.filter(id__in=ids)

        # Prefetch the M2M "members" directly (through MemberAssignment)
        qs = qs.prefetch_related(
            Prefetch('members', queryset=Member.objects.all().order_by('id'))
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)

        results = []
        for u in page:
            members_qs = u.members.all()  # from related_name='members' on Member.users
            results.append({
                "user": {
                    "id": u.id,
                    "username": getattr(u, 'username', None),
                    "email": getattr(u, 'email', None),
                },
                "members": MemberSerializer(members_qs, many=True).data
            })

        return Response({
            "success": True,
            "message": "Members grouped by user fetched successfully.",
            "results": results,
            "pagination": {
                "page": paginator.page.number,
                "total": paginator.page.paginator.count,
                "perPage": paginator.get_page_size(request),
            }
        }, status=status.HTTP_200_OK)
