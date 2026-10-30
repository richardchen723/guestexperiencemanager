"""Mount the independent owner service at a fixed path without trusting headers."""
import re
from urllib.parse import urlsplit

from werkzeug.exceptions import NotFound
from werkzeug.utils import redirect


def normalize_prefix(value):
    value = (value or '').rstrip('/')
    if value and not re.fullmatch(r'(?:/[A-Za-z0-9_-]+)+', value):
        raise ValueError('Owner path prefix must contain ordinary URL path segments.')
    return value


def public_base(origin, prefix=''):
    parsed = urlsplit(origin or '')
    if (parsed.scheme != 'https' or not parsed.netloc or parsed.path or parsed.query
            or parsed.fragment or parsed.username is not None):
        raise ValueError('Configure a valid HTTPS owner origin without a path.')
    return origin + normalize_prefix(prefix)


class OwnerMount:
    """Nginx preserves the incoming URI; only this exact prefix reaches Flask."""
    def __init__(self, application, prefix):
        self.application, self.prefix = application, normalize_prefix(prefix)

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '')
        if path == self.prefix:
            return redirect(self.prefix + '/', code=308)(environ, start_response)
        if not path.startswith(self.prefix + '/'):
            return NotFound()(environ, start_response)
        environ = dict(environ, SCRIPT_NAME=self.prefix, PATH_INFO=path[len(self.prefix):])
        return self.application(environ, start_response)
