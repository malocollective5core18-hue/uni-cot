from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('founder/saas-control/', views.founder_saas_system_control, name='founder_saas_system_control'),
    path('groups/', views.groups, name='groups'),
    path('properties/', views.properties, name='properties'),
    path('external-tables/', views.external_tables, name='external_tables'),
    # API endpoints for image posts (slider images)
    path('api/slider-images/', views.api_slider_images, name='api_slider_images'),
    path('api/slider-images/<str:image_id>/', views.api_slider_image_detail, name='api_slider_image_detail'),
    # API endpoints for countdown cards
    path('api/countdown-cards/', views.api_countdown_cards, name='api_countdown_cards'),
    path('api/countdown-cards/<str:card_id>/', views.api_countdown_card_detail, name='api_countdown_card_detail'),
    # API endpoints for properties (lost & found)
    path('api/properties/', views.api_properties, name='api_properties'),
    path('api/properties/<int:property_id>/', views.api_property_detail, name='api_property_detail'),
    # API endpoints for users
    path('api/users/', views.api_users, name='api_users'),
    path('api/users/<int:user_id>/', views.api_user_detail, name='api_user_detail'),
    # API endpoints for groups
    path('api/groups/', views.api_groups, name='api_groups'),
    path('api/groups/<int:group_id>/', views.api_group_detail, name='api_group_detail'),
    # API endpoints for group members
    path('api/group-members/', views.api_group_members, name='api_group_members'),
    path('api/group-members/<int:member_id>/', views.api_group_member_detail, name='api_group_member_detail'),
    # API endpoint for signup setting (admin controls)
    path('api/signup-setting/', views.api_signup_setting, name='api_signup_setting'),
    # API endpoint for system storage links
    path('api/system-settings/', views.api_system_settings, name='api_system_settings'),
    # API endpoints for registration form fields
    path('api/registration-fields/', views.api_registration_fields, name='api_registration_fields'),
    path('api/registration-fields/<int:field_id>/', views.api_registration_field_detail, name='api_registration_field_detail'),
    # API endpoints for form framework restructure
    path('api/users/clear-all/', views.api_users_clear_all, name='api_users_clear_all'),
    path('api/external-tables/', views.api_external_tables, name='api_external_tables'),
    path('api/external-tables/<int:table_id>/', views.api_external_table_detail, name='api_external_table_detail'),
    path('api/external-tables/<int:table_id>/records/', views.api_external_table_records, name='api_external_table_records'),
    path('api/external-tables/<int:table_id>/records/<int:record_id>/', views.api_external_table_record_detail, name='api_external_table_record_detail'),
    path('api/external-tables/<int:table_id>/toggle-visibility/', views.api_external_table_toggle_visibility, name='api_external_table_toggle_visibility'),
    path('api/validate-registration/', views.api_validate_registration, name='api_validate_registration'),
    path('api/external-tables/signup/', views.api_external_table_signup, name='api_external_table_signup'),
]
