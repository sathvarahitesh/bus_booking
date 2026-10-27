from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower


class User(AbstractUser):
    GENDER_CHOICES = [('M', 'Male'), ('F', 'Female'), ('O', 'Other')]

    phone = models.CharField(max_length=15, blank=True)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    # True for every existing account. Registration sets it to False until the email OTP is verified.
    email_verified = models.BooleanField(default=True)

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(
                Lower('email'), condition=~Q(email=''), name='unique_user_email_ci'),
        ]

    def __str__(self):
        return self.get_username()


class EmailOTP(models.Model):
    """One emailed code. Only a keyed hash of the code is stored."""
    PURPOSES = [('REGISTER', 'Registration'), ('LOGIN', 'Login'), ('RESET', 'Password reset')]
    

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='otps')
    purpose = models.CharField(max_length=10, choices=PURPOSES)
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)  # set when used or replaced

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'purpose', '-created_at'])]

    def __str__(self):
        return f'{self.user} {self.purpose} {self.created_at:%d %b %H:%M}'