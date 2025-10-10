from rest_framework import viewsets, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.db import IntegrityError
from .models import Member
from .serializers import MemberSerializer
from .pagination import MemberPagination

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
    queryset = Member.objects.all().order_by('id')
    serializer_class = MemberSerializer
    permission_classes = [AllowAny]
    pagination_class = MemberPagination

    # LIST: uses paginator above (already returns success/message)
    # If you ever want a custom message per request, uncomment and customize:
    # def list(self, request, *args, **kwargs):
    #     return super().list(request, *args, **kwargs)

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

    # DELETE
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
