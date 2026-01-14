# ssl_cert_hook.py
import os, certifi
# Buộc requests/google-auth dùng đúng bundle trong bản đóng gói
os.environ["SSL_CERT_FILE"] = certifi.where()
