from django.urls import path

from . import views


urlpatterns = [
    path('api/founder/exam-timetable-source/<str:exam_type>/', views.api_founder_exam_timetable_source, name='api_founder_exam_timetable_source'),
    path('api/timetable/exam-pdf/<str:exam_type>/', views.api_tenant_exam_timetable_pdf, name='api_tenant_exam_timetable_pdf'),
]
