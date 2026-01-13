from django.db import IntegrityError
from django.contrib.auth import get_user_model
from django.db.models import Prefetch

from rest_framework import viewsets, status, decorators
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Member, MemberAssignment
from .serializers import MemberSerializer, MemberAssignmentSerializer
from .pagination import MemberPagination

User = get_user_model()


# =================================================
# Common response helpers
# =================================================
class ResponseMixin:
    def ok(self, message="", payload=None, status_code=status.HTTP_200_OK):
        body = {"success": True, "message": message}
        if isinstance(payload, dict):
            body.update(payload)
        return Response(body, status=status_code)

    def fail(self, message="Validation error.", status_code=status.HTTP_400_BAD_REQUEST):
        return Response(
            {"success": False, "message": message},
            status=status_code
        )


# =================================================
# MEMBER CRUD
# =================================================
class MemberViewSet(ResponseMixin, viewsets.ModelViewSet):
    queryset = Member.objects.all().order_by("id")
    serializer_class = MemberSerializer
    permission_classes = [AllowAny]
    pagination_class = MemberPagination

    # ---------- CRUD ----------
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        return self.ok(
            "Member fetched successfully.",
            {"member": self.get_serializer(instance).data}
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return self.fail("Could not create member.")
        serializer.save()
        return self.ok(
            "Member created successfully.",
            {"member": serializer.data},
            status.HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        if not serializer.is_valid():
            return self.fail("Could not update member.")
        serializer.save()
        return self.ok(
            "Member updated successfully.",
            {"member": serializer.data}
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        try:
            instance.delete()
        except IntegrityError:
            return self.fail(
                "Cannot delete member due to related records.",
                status.HTTP_409_CONFLICT
            )
        return self.ok("Member deleted successfully.")

    # ---------- SERVER SAFE ----------
    @decorators.action(detail=True, methods=["post"], url_path="delete")
    def delete_via_post(self, request, *args, **kwargs):
        return self.destroy(request, *args, **kwargs)

    @decorators.action(detail=True, methods=["post"], url_path="update")
    def update_via_post(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    @decorators.action(detail=True, methods=["post"], url_path="patch")
    def patch_via_post(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    # =================================================
    # ASSIGN MEMBER TO USER (USER COMES FROM BODY)
    # POST /api/assign/members/{member_id}/assign-user/
    # Body:
    # {
    #   "user_id": <required>,
    #   "sign_in_id": <optional | null>
    # }
    # =================================================
    @decorators.action(
        detail=True,
        methods=["post"],
        url_path="assign-user",
        permission_classes=[IsAuthenticated]
    )
    def assign_user(self, request, pk=None):
        """
        pk = member_id (from URL)

        Body:
        {
            "user_id": <required>,
            "sign_in_id": <optional>
        }
        """

        # =========================
        # member from URL
        # =========================
        member = self.get_object()

        # =========================
        # user_id (MANDATORY)
        # =========================
        user_id = request.data.get("user_id")
        if not user_id:
            return self.fail("user_id is required.", status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(pk=int(user_id))
        except (User.DoesNotExist, ValueError, TypeError):
            return self.fail("Invalid user_id.", status.HTTP_404_NOT_FOUND)

        # =========================
        # sign_in_id (OPTIONAL)
        # =========================
        sign_in = None
        sign_in_id = request.data.get("sign_in_id")

        if sign_in_id not in (None, "", "null"):
            try:
                sign_in = Member.objects.get(pk=int(sign_in_id))
            except (Member.DoesNotExist, ValueError, TypeError):
                return self.fail("Invalid sign_in_id.", status.HTTP_404_NOT_FOUND)

        # =====================================================
        # CHECK EXISTING (user_id + member_id)
        # =====================================================
        assignment = MemberAssignment.objects.filter(
            user=user,
            member=member
        ).first()

        # =====================================================
        # CASE 1: ROW EXISTS
        # =====================================================
        if assignment:
            # sign_in NULL → UPDATE SAME ROW
            if assignment.sign_in is None and sign_in is not None:
                assignment.sign_in = sign_in
                assignment.save()

                return self.ok(
                    "Sign-in assigned successfully.",
                    {"assignment": MemberAssignmentSerializer(assignment).data}
                )

            # same sign_in → ALREADY ASSIGNED
            if assignment.sign_in == sign_in:
                return self.ok(
                    "Already assigned.",
                    {"assignment": MemberAssignmentSerializer(assignment).data}
                )

            # 🔥 DIFFERENT sign_in → CREATE NEW ROW (FIXED)
            new_assignment = MemberAssignment.objects.create(
                user=user,
                member=member,
                sign_in=sign_in
            )

            return self.ok(
                "Assigned with new sign-in successfully.",
                {"assignment": MemberAssignmentSerializer(new_assignment).data}
            )

        # =====================================================
        # CASE 2: NO ROW → CREATE
        # =====================================================
        assignment = MemberAssignment.objects.create(
            user=user,
            member=member,
            sign_in=sign_in
        )

        return self.ok(
            "Assigned successfully.",
            {"assignment": MemberAssignmentSerializer(assignment).data}
        )

    # =================================================
    # UNASSIGN MEMBER
    # POST /api/assign/members/{member_id}/unassign-user/
    # Body:
    # {
    #   "user_id": <required>,
    #   "sign_in_id": <optional>
    # }
    # =================================================
    @decorators.action(detail=True, methods=["post"], url_path="unassign-user")
    def unassign_user(self, request, pk=None):
        """
        pk = MemberAssignment ID

        RULE:
        - Each assignment is ONE row
        - Unassign = DELETE the row
        """

        try:
            assignment = MemberAssignment.objects.get(pk=pk)
        except MemberAssignment.DoesNotExist:
            return self.fail(
                "Assignment not found.",
                status_code=status.HTTP_404_NOT_FOUND
            )

        assignment.delete()

        return self.ok(
            "Assignment removed successfully.",
            {"id": pk}
        )


    # =================================================
    # LIST USERS FOR A MEMBER
    # GET /api/assign/members/{member_id}/users/
    # =================================================
    @decorators.action(detail=True, methods=["get"], url_path="users")
    def list_users(self, request, pk=None):
        member = self.get_object()

        assignments = (
            MemberAssignment.objects
            .filter(member=member)
            .select_related("user", "sign_in")
            .order_by("id")
        )

        return self.ok(
            "Users linked to member fetched successfully.",
            {
                "assignments": MemberAssignmentSerializer(
                    assignments, many=True
                ).data
            }
        )


# =================================================
# LIST MEMBERS FOR A USER
# GET /api/assign/users/{user_id}/members/
# =================================================
class UserMembersView(ResponseMixin, APIView):
    permission_classes = [AllowAny]
    pagination_class = MemberPagination

    def get(self, request, user_id: int):
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return self.fail(
                "User not found.",
                status.HTTP_404_NOT_FOUND
            )

        qs = (
            MemberAssignment.objects
            .filter(user=user)
            .select_related("member", "sign_in")
            .order_by("id")
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)

        return paginator.get_paginated_response({
            "success": True,
            "message": "Members for user fetched successfully.",
            "members": MemberAssignmentSerializer(
                page, many=True
            ).data
        })


# =================================================
# LIST USERS WITH THEIR MEMBERS
# GET /api/assign/users/members/
# =================================================
class UsersMembersView(APIView):
    permission_classes = [AllowAny]
    pagination_class = MemberPagination

    def get(self, request):
        qs = User.objects.all().order_by("id").prefetch_related(
            Prefetch(
                "member_assignments",
                queryset=MemberAssignment.objects.select_related(
                    "member", "sign_in"
                )
            )
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)

        results = []
        for user in page:
            results.append({
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                },
                "members": MemberAssignmentSerializer(
                    user.member_assignments.all(),
                    many=True
                ).data
            })

        return paginator.get_paginated_response({
            "success": True,
            "message": "Users with members fetched successfully.",
            "results": results
        })
