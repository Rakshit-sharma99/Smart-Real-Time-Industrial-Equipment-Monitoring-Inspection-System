from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsRestaurantStaff, staff_required

from . import services
from .models import Notification


@staff_required
def notification_list(request):
    page_obj = Paginator(Notification.objects.filter(recipient=request.user), 30).get_page(request.GET.get("page"))
    return render(request, "notifications/list.html", {
        "notifications": page_obj.object_list, "page_obj": page_obj, "page_title": "Notifications",
    })


@staff_required
def menu_fragment(request):
    """The bell dropdown (refreshed live)."""
    inbox = Notification.objects.filter(recipient=request.user)
    return render(request, "notifications/_menu.html", {
        "notifications": inbox[:8], "unread_notifications": inbox.filter(is_read=False).count(),
    })


@staff_required
@require_POST
def open_notification(request, pk):
    """Mark one notification as read and go to the page it points at."""
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    services.mark_read(request.user, ids=[notification.pk])
    link = notification.link
    return redirect(link if link.startswith("/") else "notifications:list")


@staff_required
@require_POST
def mark_all_read(request):
    services.mark_read(request.user)
    next_url = request.POST.get("next", "")
    return redirect(next_url if next_url.startswith("/") else "notifications:list")


class NotificationListApiView(APIView):
    """GET /api/v1/notifications/ - unread count and the latest 20 notifications."""

    permission_classes = [IsRestaurantStaff]

    def get(self, request):
        inbox = Notification.objects.filter(recipient=request.user)
        return Response({
            "unread": inbox.filter(is_read=False).count(),
            "results": [
                {"id": n.pk, "kind": n.kind, "title": n.title, "message": n.message, "link": n.link,
                 "is_read": n.is_read, "created_at": n.created_at.isoformat()}
                for n in inbox[:20]
            ],
        })


class NotificationReadApiView(APIView):
    """POST /api/v1/notifications/read/  {"ids": [1, 2]}  (no ids = mark all as read)."""

    permission_classes = [IsRestaurantStaff]

    def post(self, request):
        ids = request.data.get("ids")
        if ids is not None and not (isinstance(ids, list) and all(isinstance(i, int) for i in ids)):
            return Response({"error": "ids must be a list of numbers.", "details": {}}, status=400)
        changed = services.mark_read(request.user, ids=ids)
        return Response({"marked_read": changed, "unread": services.unread_count(request.user)})
