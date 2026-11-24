from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name='FounderExamTimetableSource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('type', models.CharField(choices=[('test1', 'Test 1'), ('test2', 'Test 2'), ('ue', 'UE')], max_length=10, unique=True)),
                ('file', models.FileField(blank=True, upload_to='system/exam_timetables/')),
                ('original_filename', models.CharField(blank=True, max_length=255)),
                ('version', models.PositiveIntegerField(default=0)),
                ('uploaded_by_id', models.BigIntegerField(blank=True, null=True)),
                ('uploaded_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'exams_founderexam_source',
                'ordering': ('type',),
            },
        ),
    ]
