from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Profile
from .serializers import ProfileSerializer
from django.http import FileResponse, Http404
from django.conf import settings
from PIL import Image
import os
from io import BytesIO

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
        
def resized_image_view(request):
    path = request.GET.get('path')  # e.g., /media/profile_images/image.jpg
    width = int(request.GET.get('w', 200))  # default 200px
    height = int(request.GET.get('h', 200))  # default 200px

    if not path or not path.startswith('/media/'):
        raise Http404("Invalid image path")

    # Full path on the filesystem
    full_path = os.path.join(settings.BASE_DIR, path.strip('/'))

    if not os.path.exists(full_path):
        raise Http404("Image not found")

    try:
        with Image.open(full_path) as img:
            img = img.convert("RGB")  # Ensures compatibility
            img = img.resize((width, height))
            buffer = BytesIO()
            img.save(buffer, format='JPEG')
            buffer.seek(0)
            return FileResponse(buffer, content_type='image/jpeg')
    except Exception as e:
        raise Http404(f"Error processing image: {str(e)}")