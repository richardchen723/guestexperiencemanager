"""Run separately: gunicorn --bind 127.0.0.1:5011 wsgi_owner:app"""
from owner_portal.app import create_app

app = create_app()
