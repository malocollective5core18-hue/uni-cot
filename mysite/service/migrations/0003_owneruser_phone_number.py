from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("service", "0002_comment_dislikes_comment_likes_comment_rating_reply"),
    ]

    operations = [
        migrations.AddField(
            model_name="owneruser",
            name="phone_number",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
