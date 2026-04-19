from django.urls import path
from . import views

app_name = 'service'

urlpatterns = [
    path('', views.welcome, name='welcome'),
    path('welcome/', views.welcome, name='welcome'),
    path('service/welcome/', views.service_welcome, name='service_welcome'),
    path('system/', views.system_demo, name='system_demo'),
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
    path('owner-dashboard/', views.owner_dashboard, name='owner_dashboard'),
    path('member-dashboard/', views.member_dashboard, name='member_dashboard'),
    path('add-comment/', views.add_comment, name='add_comment'),
    path('comment/<int:comment_id>/reply/', views.reply_comment, name='reply_comment'),
    path('comment/<int:comment_id>/<str:reaction>/', views.react_comment, name='react_comment'),
    path('register-member/', views.register_member, name='register_member'),
    path('moderate-comment/<int:comment_id>/<str:action>/', views.moderate_comment, name='moderate_comment'),
    path('subscription-expired/', views.subscription_expired, name='subscription_expired'),

    
    # API Endpoints
    path('api/create-tenant/', views.api_create_tenant, name='api_create_tenant'),
    path('api/owner-admin-login/', views.api_owner_admin_login, name='api_owner_admin_login'),
    path('api/create-member/', views.api_create_member, name='api_create_member'),
    path('api/post-comment/', views.api_post_comment, name='api_post_comment'),
    path('api/moderate-comment/', views.api_moderate_comment, name='api_moderate_comment'),
]
