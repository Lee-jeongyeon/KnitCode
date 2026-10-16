from django.urls import path
from django.views import View
from rest_framework import viewsets


class Home(View):
    def get(self, request):
        return 1

    def render(self):
        return 2


def about(request):
    return 3


def hidden(request):
    return 4


class ItemViewSet(viewsets.ModelViewSet):
    def list(self, request):
        return 1

    def create(self, request):
        return 2

    def helper(self):
        return 3


urlpatterns = [
    path("", Home.as_view()),
    path("about/", about),
]
