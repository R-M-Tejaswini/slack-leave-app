# leavebot/leavebot/settings.py
"""
Django settings for the leavebot project.

This file contains the core configuration for the Django application, including
database settings, application definitions, middleware, and security keys.
It is configured to load sensitive information from a .env file for security
and portability.
"""

from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

# ==============================================================================
# CORE SETTINGS
# ==============================================================================

# Build paths inside the project like this: BASE_DIR / 'subdir'
BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = '3s46c_-r!p6jh#+q_u7!gwy+6hycu7*2_ofy0jq$r@a)=h_hou'
# The DEBUG flag is loaded as a boolean from an environment variable.
DEBUG = os.getenv('DJANGO_DEBUG', 'True') == 'True'

# Define the allowed hosts. For production, this should be your domain name.
# The production domain should be loaded from an environment variable.
ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "25c1eb4912b2.ngrok-free.app",  # Example for local development with ngrok
    os.getenv('PRODUCTION_HOST'), # e.g., your ngrok URL or final domain
]


# ==============================================================================
# APPLICATION-SPECIFIC SETTINGS (Loaded from Environment Variables)
# ==============================================================================

SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_REQUEST_CHANNEL = os.getenv("SLACK_REQUEST_CHANNEL")


# ==============================================================================
# DJANGO-SPECIFIC CONFIGURATION
# ==============================================================================

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'slackapp.apps.SlackappConfig', # Use the AppConfig for better structure
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'leavebot.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'leavebot.wsgi.application'


# Database Configuration
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/
STATIC_URL = 'static/'


# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Logging Configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': 'slack_leave_app.log',
        },
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'slackapp': {
            'handlers': ['file', 'console'],
            'level': 'INFO',
            'propagate': True,
        },
    },
}

# ==============================================================================
# CELERY CONFIGURATION
# ==============================================================================
# URL for the Redis message broker.
CELERY_BROKER_URL = 'redis://localhost:6379/0'
# URL for the result backend (can be the same as the broker).
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
# Use JSON as the content type for tasks.
CELERY_ACCEPT_CONTENT = ['json']
# Use JSON as the task serializer.
CELERY_TASK_SERIALIZER = 'json'