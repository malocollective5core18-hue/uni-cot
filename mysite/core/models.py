from django.db import models
from django.contrib.postgres.indexes import GinIndex
import uuid
import json

# Helper function for generating UUIDs (avoid lambda in model defaults)
def generate_uuid():
    return str(uuid.uuid4())

# Create your models here.

class CountdownCard(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('cancelled', 'Cancelled'),
        ('completed', 'Completed'),
    ]
    
    id = models.CharField(max_length=36, primary_key=True, default=generate_uuid)
    title = models.TextField()
    description = models.TextField()
    file_url = models.TextField()
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    created_by = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.IntegerField(default=1)
    is_published = models.BooleanField(default=True)

    class Meta:
        db_table = 'countdown_cards'
        ordering = ['-created_at']
        verbose_name_plural = 'Countdown Cards'

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = str(uuid.uuid4())
        super().save(*args, **kwargs)


class ImagePost(models.Model):
    CATEGORY_CHOICES = [
        ('critical', 'Critical'),
        ('important', 'Important'),
        ('entertainment', 'Entertainment'),
        ('additional', 'Additional'),
    ]
    
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
    ]
    
    id = models.CharField(max_length=36, primary_key=True, default=generate_uuid)
    title = models.TextField()
    description = models.TextField(blank=True, null=True)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='important')
    cloudinary_public_id = models.TextField(blank=True, null=True)
    cloudinary_url = models.TextField()
    cloudinary_format = models.CharField(max_length=50, blank=True, null=True)
    target_url = models.TextField(blank=True, null=True)
    display_order = models.IntegerField(default=0)
    expires_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    created_by = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.IntegerField(default=1)

    class Meta:
        db_table = 'image_posts'
        ordering = ['display_order', '-created_at']
        verbose_name = 'Image Post'
        verbose_name_plural = 'Image Posts'

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = str(uuid.uuid4())
        super().save(*args, **kwargs)


# ============================================
# USER MANAGEMENT MODELS
# ============================================

class User(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('flagged', 'Flagged'),
        ('inactive', 'Inactive'),
        ('banned', 'Banned'),
    ]
    
    ROLE_CHOICES = [
        ('member', 'Member'),
        ('admin', 'Admin'),
        ('moderator', 'Moderator'),
    ]
    
    id = models.AutoField(primary_key=True)
    uuid = models.CharField(max_length=36, default=generate_uuid, unique=True)
    full_name = models.CharField(max_length=255)
    registration_number = models.CharField(max_length=100, unique=True)
    email = models.CharField(max_length=255, unique=True, blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    password_hash = models.CharField(max_length=255, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)
    is_admin = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    group_name = models.CharField(max_length=100, blank=True, null=True)
    role = models.CharField(max_length=50, choices=ROLE_CHOICES, default='member')
    case_info = models.TextField(blank=True, null=True)
    flagged_reason = models.TextField(blank=True, null=True)
    flagged_by = models.IntegerField(blank=True, null=True)
    flagged_at = models.DateTimeField(blank=True, null=True)
    last_login = models.DateTimeField(blank=True, null=True)
    login_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.IntegerField(blank=True, null=True)

    class Meta:
        db_table = 'users'
        ordering = ['-created_at']

    def __str__(self):
        return self.full_name


class UserGroup(models.Model):
    id = models.AutoField(primary_key=True)
    group_name = models.CharField(max_length=100, unique=True)
    group_code = models.CharField(max_length=20, unique=True, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    max_members = models.IntegerField(default=50)
    current_members = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    is_flagged = models.BooleanField(default=False)
    flagged_reason = models.TextField(blank=True, null=True)
    leader_id = models.IntegerField(blank=True, null=True)
    created_by = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_groups'
        ordering = ['group_name']

    def __str__(self):
        return self.group_name


class UserGroupMember(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('pending', 'Pending'),
        ('removed', 'Removed'),
    ]
    
    id = models.AutoField(primary_key=True)
    user_id = models.IntegerField()
    group_id = models.IntegerField()
    joined_at = models.DateTimeField(auto_now_add=True)
    is_leader = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')

    class Meta:
        db_table = 'user_group_members'
        unique_together = ('user_id', 'group_id')

    def __str__(self):
        return f"User {self.user_id} in Group {self.group_id}"


class Property(models.Model):
    CATEGORY_CHOICES = [
        ('lost', 'Lost'),
        ('found', 'Found'),
        ('claimed', 'Claimed'),
        ('unclaimed', 'Unclaimed'),
    ]
    
    id = models.AutoField(primary_key=True)
    item_name = models.CharField(max_length=255)
    description = models.TextField()
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='lost')
    location = models.CharField(max_length=255, blank=True, null=True)
    date_found = models.DateTimeField(blank=True, null=True)
    image_url = models.TextField(blank=True, null=True)
    contact_info = models.CharField(max_length=255, blank=True, null=True)
    reported_by = models.IntegerField(blank=True, null=True)
    claimed_by = models.IntegerField(blank=True, null=True)
    claimed_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=20, default='open')
    created_by = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'properties'
        ordering = ['-created_at']

    def __str__(self):
        return self.item_name


class SystemSetting(models.Model):
    SETTING_TYPE_CHOICES = [
        ('string', 'String'),
        ('integer', 'Integer'),
        ('boolean', 'Boolean'),
        ('json', 'JSON'),
    ]
    
    id = models.AutoField(primary_key=True)
    setting_key = models.CharField(max_length=100)
    setting_value = models.TextField(blank=True, null=True)
    setting_type = models.CharField(max_length=20, choices=SETTING_TYPE_CHOICES, default='string')
    description = models.TextField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.IntegerField(blank=True, null=True)
    created_by = models.IntegerField(blank=True, null=True)

    class Meta:
        db_table = 'system_settings'
        constraints = [
            models.UniqueConstraint(fields=['setting_key', 'created_by'], name='uniq_setting_per_owner'),
        ]

    def __str__(self):
        return self.setting_key


class RegistrationFormField(models.Model):
    """Model to store registration form field definitions"""
    FIELD_TYPE_CHOICES = [
        ('text', 'Text'),
        ('email', 'Email'),
        ('number', 'Number'),
        ('date', 'Date'),
        ('select', 'Select/Dropdown'),
        ('textarea', 'Text Area'),
        ('tel', 'Phone Number'),
    ]
    
    id = models.AutoField(primary_key=True)
    field_name = models.CharField(max_length=100)
    field_key = models.CharField(max_length=100, help_text="Unique identifier for the field (e.g., full_name, email)")
    field_type = models.CharField(max_length=20, choices=FIELD_TYPE_CHOICES, default='text')
    field_label = models.CharField(max_length=255)
    placeholder = models.CharField(max_length=255, blank=True, null=True)
    options = models.TextField(blank=True, null=True, help_text="JSON array for select options")
    is_required = models.BooleanField(default=False)
    display_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.IntegerField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'registration_form_fields'
        ordering = ['display_order', 'id']

    def __str__(self):
        return self.field_label


# ============================================
# EXTERNAL TABLES MODELS
# ============================================

class ExternalTable(models.Model):
    id = models.AutoField(primary_key=True)
    table_name = models.CharField(max_length=100, unique=True, db_index=True)
    fields_schema = models.JSONField(default=list, blank=True)
    hidden_columns = models.JSONField(default=list, blank=True)
    record_count = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    is_visible = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.IntegerField(blank=True, null=True, db_index=True)
    
    class Meta:
        db_table = 'external_tables'
    
    def __str__(self):
        return self.table_name
    
    def clean(self):
        if isinstance(self.fields_schema, str):
            try:
                self.fields_schema = json.loads(self.fields_schema)
            except json.JSONDecodeError:
                self.fields_schema = []
        if not isinstance(self.fields_schema, list):
            self.fields_schema = []
        if not isinstance(self.hidden_columns, list):
            self.hidden_columns = []
    
    def get_fields_list(self):
        if isinstance(self.fields_schema, list):
            return self.fields_schema
        if isinstance(self.fields_schema, str):
            try:
                return json.loads(self.fields_schema)
            except:
                return []
        return []
    
    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class ExternalTableRecord(models.Model):
    id = models.AutoField(primary_key=True)
    table = models.ForeignKey(ExternalTable, on_delete=models.CASCADE, related_name='records', db_index=True)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    
    class Meta:
        db_table = 'external_table_records'
        indexes = [
            models.Index(fields=['table', '-created_at']),
            GinIndex(fields=['data']),
        ]
    
    def __str__(self):
        return f"Record {self.id} in {self.table.table_name}"
    
    def clean(self):
        if isinstance(self.data, str):
            try:
                self.data = json.loads(self.data)
            except json.JSONDecodeError:
                self.data = {}
        if not isinstance(self.data, dict):
            self.data = {}
    
    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
