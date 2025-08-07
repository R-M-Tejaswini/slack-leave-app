#leavebot/slackapp/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("commands/",     views.slash_command,    name="slash_command"),
    path("interactions/", views.interactions,     name="interactions"),
    
]
