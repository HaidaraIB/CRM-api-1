"""
Encryption utilities for sensitive data (Access Tokens, Refresh Tokens)
"""
from cryptography.fernet import Fernet
from django.conf import settings
import base64
import os


def get_encryption_key():
    """
    الحصول على مفتاح التشفير من settings أو إنشاء واحد جديد
    """
    key = getattr(settings, 'INTEGRATION_ENCRYPTION_KEY', None)
    
    if not key:
        # في Production، يجب إضافة INTEGRATION_ENCRYPTION_KEY في settings
        # للاختبار، نستخدم مفتاح افتراضي (⚠️ لا تستخدم في Production!)
        key = os.getenv('INTEGRATION_ENCRYPTION_KEY')
        
        if not key:
            # Generate a key (only for development)
            key = Fernet.generate_key().decode()
            import warnings
            warnings.warn(
                "INTEGRATION_ENCRYPTION_KEY not set. Using generated key. "
                "This is NOT secure for production!",
                UserWarning
            )
    
    # Ensure key is bytes
    if isinstance(key, str):
        key = key.encode()
    
    return key


def encrypt_token(token):
    """
    تشفير Token
    """
    if not token:
        return None
    
    try:
        key = get_encryption_key()
        fernet = Fernet(key)
        encrypted = fernet.encrypt(token.encode())
        return encrypted.decode()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error encrypting token: {str(e)}")
        # في حالة الخطأ، نرجع Token بدون تشفير (للتوافق مع البيانات القديمة)
        return token


def decrypt_token(encrypted_token):
    """
    فك تشفير Token
    """
    if not encrypted_token:
        return None
    
    try:
        key = get_encryption_key()
        fernet = Fernet(key)
        decrypted = fernet.decrypt(encrypted_token.encode())
        return decrypted.decode()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Error decrypting token (might be unencrypted): {str(e)}")
        # إذا فشل فك التشفير، قد يكون Token غير مشفر (للتوافق مع البيانات القديمة)
        return encrypted_token



