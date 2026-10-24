from django.urls import path

from . import views

app_name = 'builder'

urlpatterns = [
    path('', views.home, name='home'),
    path('api/generate/', views.api_generate, name='api-generate'),
    path('api/job/<int:job_id>/status/', views.api_job_status, name='api-job-status'),
    path('api/job/<int:job_id>/plan/', views.api_job_plan, name='api-job-plan'),
    path('api/job/<int:job_id>/render/', views.api_job_render, name='api-job-render'),
    path('api/job/<int:job_id>/download/', views.api_job_download, name='api-job-download'),
]
