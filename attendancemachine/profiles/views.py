from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Profile
from .serializers import ProfileSerializer

class UpdateProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request):
        profile = request.user.profile  # Thanks to OneToOneField with related_name='profile'
        serializer = ProfileSerializer(profile, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({"message": "Profile updated successfully", "profile": serializer.data})
        return Response(serializer.errors, status=400)