import hmac

from flask import Flask

import config


def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')

    if config.BASIC_AUTH_USER and config.BASIC_AUTH_PASS:
        @app.before_request
        def check_auth():
            from flask import request, Response
            auth = request.authorization
            if (not auth
                    or not hmac.compare_digest(auth.username, config.BASIC_AUTH_USER)
                    or not hmac.compare_digest(auth.password, config.BASIC_AUTH_PASS)):
                return Response(
                    'Authentication required', 401,
                    {'WWW-Authenticate': 'Basic realm="El Al Flight Finder"'}
                )

    from web.routes import api, main
    app.register_blueprint(api)
    app.register_blueprint(main)
    return app
