from django.db import models


class OwnerUser(models.Model):
    """Owner/CR user model - manages programs and members"""
    email = models.EmailField(max_length=255, unique=True)
    program_name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=32, blank=True, default="")
    password = models.CharField(max_length=255)
    is_owner = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'owner_users'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.email} ({self.program_name})"


class Member(models.Model):
    """Member model - linked to OwnerUser programs"""
    owner = models.ForeignKey(OwnerUser, on_delete=models.CASCADE, related_name='members')
    reg_number = models.CharField(max_length=100, unique=True)
    program_name = models.CharField(max_length=255)
    password = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'members'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.reg_number} ({self.program_name})"


class Comment(models.Model):
    """Comment model for member reviews with ratings"""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='comments')
    owner = models.ForeignKey(OwnerUser, on_delete=models.CASCADE, related_name='comments')
    content = models.TextField()
    rating = models.IntegerField(default=5, choices=[(i, str(i)) for i in range(1, 6)])
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    likes = models.IntegerField(default=0)
    dislikes = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'comments'
        ordering = ['-created_at']

    def __str__(self):
        return f"Comment by {self.member.reg_number}"


class Reply(models.Model):
    """Reply model for comment responses"""
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name='replies')
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='replies')
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'comment_replies'
        ordering = ['created_at']

    def __str__(self):
        return f"Reply by {self.member.reg_number}"
