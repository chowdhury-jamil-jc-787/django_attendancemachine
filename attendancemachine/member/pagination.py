from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

class MemberPagination(PageNumberPagination):
    # ?page=1&perPage=10
    page_query_param = 'page'
    page_size_query_param = 'perPage'
    page_size = 10
    max_page_size = 200

    def get_paginated_response(self, data):
        return Response({
            "success": True,
            "message": "Members fetched successfully.",
            "members": data,
            "pagination": {
                "page": self.page.number,
                "total": self.page.paginator.count,     # total items
                "perPage": self.get_page_size(self.request),
            }
        })
