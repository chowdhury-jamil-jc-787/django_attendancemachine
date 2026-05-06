# notifications/views.py

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .serializers import UserDeviceTokenSerializer
from .models import UserDeviceToken


class SaveDeviceTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = UserDeviceTokenSerializer(
            data=request.data,
            context={"request": request}
        )

        if serializer.is_valid():
            serializer.save()
            return Response({
                "success": True,
                "message": "Device token saved successfully.",
                "device": serializer.data
            }, status=201)

        return Response({
            "success": False,
            "message": "Could not save device token.",
            "errors": serializer.errors
        }, status=400)


class DeleteDeviceTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.data.get("token")

        if not token:
            return Response({
                "success": False,
                "message": "token is required."
            }, status=400)

        UserDeviceToken.objects.filter(
            user=request.user,
            token=token
        ).update(is_active=False)

        return Response({
            "success": True,
            "message": "Device token removed successfully."
        })