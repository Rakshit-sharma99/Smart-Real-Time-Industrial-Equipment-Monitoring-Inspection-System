from .permissions import get_restaurant, get_role


class RestaurantMiddleware:
    """
    Puts request.role and request.restaurant on every request, so views and
    templates always filter by the logged-in staff member's restaurant.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.role = get_role(request.user)
        request.restaurant = get_restaurant(request.user)
        return self.get_response(request)
