from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Profile
from .serializers import ProfileSerializer

class UpdateProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        profile = request.user.profile
        serializer = ProfileSerializer(profile, data=request.data, partial=True, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response({"message": "Profile updated successfully", "profile": serializer.data})
        return Response(serializer.errors, status=400)
    
class RetrieveProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            profile = request.user.profile  # via OneToOneField
            serializer = ProfileSerializer(profile)
            return Response(serializer.data, status=200)
        except Profile.DoesNotExist:
            return Response({"error": "Profile not found"}, status=404)