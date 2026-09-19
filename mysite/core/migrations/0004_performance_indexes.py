from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_property_claim_and_identity_fields'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='countdowncard',
            index=models.Index(fields=['status', 'is_published', 'created_by', '-created_at'], name='core_cc_status_idx'),
        ),
        migrations.AddIndex(
            model_name='countdowncard',
            index=models.Index(fields=['created_by', '-created_at'], name='core_cc_owner_idx'),
        ),
        migrations.AddIndex(
            model_name='imagepost',
            index=models.Index(fields=['status', 'created_by', 'display_order', '-created_at'], name='core_img_status_idx'),
        ),
        migrations.AddIndex(
            model_name='imagepost',
            index=models.Index(fields=['created_by', 'category'], name='core_img_owner_cat_idx'),
        ),
        migrations.AddIndex(
            model_name='user',
            index=models.Index(fields=['created_by', 'is_active', '-created_at'], name='core_user_owner_active_idx'),
        ),
        migrations.AddIndex(
            model_name='user',
            index=models.Index(fields=['created_by', 'status'], name='core_user_owner_status_idx'),
        ),
        migrations.AddIndex(
            model_name='user',
            index=models.Index(fields=['created_by', 'group_name'], name='core_user_owner_group_idx'),
        ),
        migrations.AddIndex(
            model_name='user',
            index=models.Index(fields=['group_name', 'is_active'], name='core_user_group_active_idx'),
        ),
        migrations.AddIndex(
            model_name='usergroup',
            index=models.Index(fields=['created_by', 'is_active', 'group_name'], name='core_grp_owner_active_idx'),
        ),
        migrations.AddIndex(
            model_name='usergroup',
            index=models.Index(fields=['created_by', 'is_flagged'], name='core_grp_owner_flag_idx'),
        ),
        migrations.AddIndex(
            model_name='usergroup',
            index=models.Index(fields=['leader_id'], name='core_grp_leader_idx'),
        ),
        migrations.AddIndex(
            model_name='usergroupmember',
            index=models.Index(fields=['group_id', 'status', 'joined_at'], name='core_gm_group_status_idx'),
        ),
        migrations.AddIndex(
            model_name='usergroupmember',
            index=models.Index(fields=['user_id', 'status'], name='core_gm_user_status_idx'),
        ),
        migrations.AddIndex(
            model_name='usergroupmember',
            index=models.Index(fields=['user_id', 'group_id', 'status'], name='core_gm_user_group_status_idx'),
        ),
        migrations.AddIndex(
            model_name='property',
            index=models.Index(fields=['created_by', 'category', 'status', '-created_at'], name='core_prop_owner_cat_stat_idx'),
        ),
        migrations.AddIndex(
            model_name='property',
            index=models.Index(fields=['created_by', 'status', '-created_at'], name='core_prop_owner_status_idx'),
        ),
        migrations.AddIndex(
            model_name='property',
            index=models.Index(fields=['registration_number'], name='core_prop_reg_idx'),
        ),
        migrations.AddIndex(
            model_name='systemsetting',
            index=models.Index(fields=['created_by', 'setting_key'], name='core_set_owner_key_idx'),
        ),
        migrations.AddIndex(
            model_name='registrationformfield',
            index=models.Index(fields=['created_by', 'is_active', 'display_order', 'id'], name='core_reg_owner_active_idx'),
        ),
        migrations.AddIndex(
            model_name='externaltable',
            index=models.Index(fields=['created_by', 'is_active', 'is_visible'], name='core_ext_owner_visible_idx'),
        ),
        migrations.AddIndex(
            model_name='externaltable',
            index=models.Index(fields=['created_by', 'table_name'], name='core_ext_owner_name_idx'),
        ),
    ]
